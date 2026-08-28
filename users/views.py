from rest_framework import generics, permissions, views, status, response
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_str, force_bytes
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework_simplejwt.views import TokenObtainPairView
from .serializers import UserRegisterSerializer, UserSerializer, CustomTokenObtainPairSerializer
from .tokens import email_verification_token_generator
import logging

logger = logging.getLogger(__name__)
User = get_user_model()

class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [permissions.AllowAny]

class RegisterView(generics.CreateAPIView):
    serializer_class = UserRegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        # New users default to is_email_verified = False
        user.is_email_verified = False
        user.save()
        
        # Send verification email
        try:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = email_verification_token_generator.make_token(user)
            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173').rstrip('/')
            verification_link = f"{frontend_url}/verify-email/{uid}/{token}"
            
            subject = "Verify your email - Finance Manager"
            message = f"Hi {user.username},\n\nThank you for registering on Finance Manager. Please verify your email address by clicking the link below:\n\n{verification_link}\n\nThis link will expire in 3 days.\n\nBest regards,\nFinance Manager Team"
            
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
        except Exception as e:
            logger.error(f"Error sending verification email during registration: {e}")
            # We don't fail registration if mail delivery configuration has issues locally,
            # but we log it. In production, SMTP should be properly configured.

        return response.Response({
            "detail": "Registration successful. Please check your email to verify your account."
        }, status=status.HTTP_201_CREATED)

class VerifyEmailView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        uidb64 = request.data.get('uid')
        token = request.data.get('token')
        
        if not uidb64 or not token:
            return response.Response({"detail": "Missing UID or token."}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return response.Response({"detail": "Invalid verification link."}, status=status.HTTP_400_BAD_REQUEST)

        if email_verification_token_generator.check_token(user, token):
            user.is_email_verified = True
            user.save()
            return response.Response({"detail": "Email verified successfully."})
            
        return response.Response({"detail": "Verification link has expired or is invalid."}, status=status.HTTP_400_BAD_REQUEST)

class ResendVerificationView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return response.Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            user = User.objects.get(email=email)
            if not user.is_email_verified:
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = email_verification_token_generator.make_token(user)
                frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173').rstrip('/')
                verification_link = f"{frontend_url}/verify-email/{uid}/{token}"
                
                subject = "Verify your email - Finance Manager"
                message = f"Hi {user.username},\n\nPlease verify your email address by clicking the link below:\n\n{verification_link}\n\nThis link will expire in 3 days.\n\nBest regards,\nFinance Manager Team"
                
                send_mail(
                    subject,
                    message,
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
        except User.DoesNotExist:
            pass # Prevent account enumeration
        except Exception as e:
            logger.error(f"Error resending verification email: {e}")
            
        return response.Response({"detail": "If a matching unverified account was found, a verification email has been sent."})

class ForgotPasswordView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return response.Response({"detail": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            user = User.objects.get(email=email)
            # Only allow password reset for verified accounts
            if user.is_email_verified:
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:5173').rstrip('/')
                reset_link = f"{frontend_url}/reset-password/{uid}/{token}"
                
                subject = "Password Reset Request - Finance Manager"
                message = f"Hi {user.username},\n\nYou requested a password reset for your Finance Manager account. Please click the link below to set a new password:\n\n{reset_link}\n\nThis link is single-use and will expire shortly.\n\nIf you did not request this, you can ignore this email.\n\nBest regards,\nFinance Manager Team"
                
                send_mail(
                    subject,
                    message,
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
        except User.DoesNotExist:
            pass # Prevent account enumeration
        except Exception as e:
            logger.error(f"Error sending forgot password email: {e}")
            
        return response.Response({"detail": "If a matching verified account was found, a password reset link has been sent."})

class ResetPasswordView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        uidb64 = request.data.get('uid')
        token = request.data.get('token')
        password = request.data.get('password')
        confirm_password = request.data.get('confirm_password')
        
        if not uidb64 or not token or not password:
            return response.Response({"detail": "Missing required fields."}, status=status.HTTP_400_BAD_REQUEST)
            
        if password != confirm_password:
            return response.Response({"detail": "Passwords do not match."}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return response.Response({"detail": "Invalid reset link."}, status=status.HTTP_400_BAD_REQUEST)
            
        if not user.is_email_verified:
            return response.Response({"detail": "Only verified accounts can reset password."}, status=status.HTTP_400_BAD_REQUEST)
            
        if not default_token_generator.check_token(user, token):
            return response.Response({"detail": "Reset link has expired or is invalid."}, status=status.HTTP_400_BAD_REQUEST)
            
        # Validate password strength using Django's default validators
        try:
            validate_password(password, user)
        except DjangoValidationError as e:
            return response.Response({"detail": e.messages[0]}, status=status.HTTP_400_BAD_REQUEST)
            
        user.set_password(password)
        user.save()
        return response.Response({"detail": "Password has been reset successfully."})

class CurrentUserView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return response.Response(serializer.data)

class LogoutView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if not refresh_token:
                return response.Response({"error": "Refresh token is required"}, status=status.HTTP_400_BAD_REQUEST)
            
            token = RefreshToken(refresh_token)
            token.blacklist()
            return response.Response({"success": "Successfully logged out"}, status=status.HTTP_205_RESET_CONTENT)
        except Exception as e:
            return response.Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

class UsersExistView(views.APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        exists = User.objects.exists()
        return response.Response({"exists": exists})
