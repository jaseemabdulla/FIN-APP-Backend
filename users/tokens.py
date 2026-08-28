from django.contrib.auth.tokens import PasswordResetTokenGenerator

class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        # Incorporate is_email_verified in the hash so that once verified, the token is invalidated.
        return (
            str(user.pk) + str(timestamp) +
            str(user.is_email_verified) + str(user.password) + str(user.last_login)
        )

email_verification_token_generator = EmailVerificationTokenGenerator()
