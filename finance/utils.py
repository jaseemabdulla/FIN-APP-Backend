from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from io import BytesIO
from django.db.models import Sum
from collections import defaultdict
import datetime

def generate_pdf_report(title, date_range_str, transactions, total_income, total_expense, daily_balances=None):
    if daily_balances is None:
        daily_balances = {}
        
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = styles['Title']
    heading_style = styles['Heading2']
    sub_heading_style = styles['Heading3']
    normal_style = styles['Normal']
    
    # Custom styles
    desc_style = ParagraphStyle(
        'Desc',
        parent=normal_style,
        fontSize=9,
        leading=11
    )

    # Title and Date
    elements.append(Paragraph(title, title_style))
    elements.append(Paragraph(f"Period: {date_range_str}", normal_style))
    elements.append(Spacer(1, 20))

    # Summary Section
    elements.append(Paragraph("Overall Summary", heading_style))
    net_savings = total_income - total_expense
    
    # Calculate Total Inflow (Credit) and Total Outflow (Debit)
    total_credit = sum(txn.amount for txn in transactions if txn.transaction_type in ['INCOME', 'DEBT_TAKEN', 'DEBT_GIVEN_RETURN', 'FUND_MANAGEMENT_INC'])
    total_debit = sum(txn.amount for txn in transactions if txn.transaction_type in ['EXPENSE', 'INVESTMENT', 'DEBT_GIVEN', 'DEBT_TAKEN_RETURN', 'FUND_MANAGEMENT_DEC'])
    
    # Calculate Remaining Amount Have (closing balance at the end of the period)
    remaining_amount = 0
    if daily_balances:
        sorted_dates = sorted(daily_balances.keys())
        if sorted_dates:
            last_date = sorted_dates[-1]
            # Handle possible dict or single values
            cl_data = daily_balances[last_date]['closing']
            if isinstance(cl_data, dict):
                remaining_amount = cl_data.get('total', 0)
            else:
                remaining_amount = cl_data
            
    if not remaining_amount and transactions.exists():
        last_txn_date = max(txn.date for txn in transactions)
        from .models import BalanceSnapshot
        latest_snap = BalanceSnapshot.objects.filter(date__lte=last_txn_date).order_by('-date').first()
        if latest_snap:
            remaining_amount = latest_snap.total_balance

    net_diff = total_credit - total_debit
    diff_str = f"+{net_diff}" if net_diff >= 0 else f"{net_diff}"

    summary_data = [
        ['Total Income', f"+{total_income}"],
        ['Total Expense', f"-{total_expense}"],
        ['Net Savings', f"{net_savings}"],
        ['Total Inflow (Credit)', f"+{total_credit}"],
        ['Total Outflow (Debit/Spent)', f"-{total_debit}"],
        ['Net Difference (Credit - Debit)', f"{diff_str}"]
    ]
    
    summary_table = Table(summary_data, colWidths=[220, 100])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (1, 0), colors.HexColor('#e6ffe6')), # Light green for income
        ('BACKGROUND', (0, 1), (1, 1), colors.HexColor('#ffe6e6')), # Light red for expense
        ('BACKGROUND', (0, 2), (1, 2), colors.HexColor('#e6f7ff')), # Light blue for net savings
        ('BACKGROUND', (0, 3), (1, 3), colors.HexColor('#e6fffa')), # Mint green for credit
        ('BACKGROUND', (0, 4), (1, 4), colors.HexColor('#fff2e6')), # Light orange for debit
        ('BACKGROUND', (0, 5), (1, 5), colors.HexColor('#f2e6ff')), # Light purple for remaining
        ('TEXTCOLOR', (1, 0), (1, 0), colors.green),
        ('TEXTCOLOR', (1, 1), (1, 1), colors.red),
        ('TEXTCOLOR', (1, 3), (1, 3), colors.HexColor('#008060')),
        ('TEXTCOLOR', (1, 4), (1, 4), colors.HexColor('#cc5200')),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (1, 0), (1, 5), 'RIGHT'),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 25))

    # Detailed Transactions grouped by Date
    elements.append(Paragraph("Daily Details", heading_style))
    
    # Group by date
    grouped_txns = defaultdict(list)
    for txn in transactions:
        grouped_txns[txn.date].append(txn)
    
    sorted_dates = sorted(grouped_txns.keys())
    
    for date_obj in sorted_dates:
        day_txns = grouped_txns[date_obj]
        
        # Day Header with Balance
        day_str = date_obj.strftime("%A, %d %B %Y")
        
        # Get balances
        balances = daily_balances.get(date_obj, {'opening': {'total': 0, 'cash': 0, 'account': 0}, 'closing': {'total': 0, 'cash': 0, 'account': 0}})
        
        op_data = balances['opening']
        cl_data = balances['closing']

        # Handle simplified structure if passed (though we updated views)
        if not isinstance(op_data, dict):
             op_data = {'total': op_data, 'cash': 0, 'account': 0}
        if not isinstance(cl_data, dict):
             cl_data = {'total': cl_data, 'cash': 0, 'account': 0}

        op_text = f"Total: {op_data['total']} (Cash: {op_data['cash']}, A/c: {op_data['account']})"
        cl_text = f"Total: {cl_data['total']} (Cash: {cl_data['cash']}, A/c: {cl_data['account']})"
        
        header_text = f"{day_str}  <font size=9 color='grey'>(Opening: {op_text})</font>"
        elements.append(Paragraph(header_text, sub_heading_style))
        
        # Table Header
        txn_data = [['Category', 'Description', 'Type', 'Amount', 'Mode']]
        
        day_income = 0
        day_expense = 0
        
        for txn in day_txns:
            # Income: Only INCOME type and NOT a repayment/linked to debt
            if txn.transaction_type == 'INCOME' and not txn.related_debt:
                day_income += txn.amount
            # Expense: Only EXPENSE type and NOT a repayment/linked to debt
            elif txn.transaction_type == 'EXPENSE' and not txn.related_debt:
                day_expense += txn.amount

            # Wrap description
            desc_para = Paragraph(txn.description, desc_style)
            
            txn_data.append([
                str(txn.category) if txn.category else '-',
                desc_para,
                txn.transaction_type,
                txn.amount,
                txn.payment_mode
            ])

        # Create Table
        # Widths: Cat(90), Desc(190), Type(80), Amt(70), Mode(60) -> 490 total
        t = Table(txn_data, colWidths=[90, 190, 80, 70, 60])
        
        style_cmds = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (1, 1), (1, -1), 'LEFT'), # Description align left
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
        ]
        
        for i, txn in enumerate(day_txns, start=1):
             color = colors.green if txn.transaction_type in ['INCOME', 'DEBT_TAKEN', 'DEBT_GIVEN_RETURN', 'FUND_MANAGEMENT_INC'] else colors.red
             style_cmds.append(('TEXTCOLOR', (3, i), (3, i), color))
        
        t.setStyle(TableStyle(style_cmds))
        
        elements.append(t)
        
        # Daily Total and Closing Balance
        daily_summary_text = (
            f"<b>Daily Income:</b> <font color='green'>+{day_income}</font>  |  "
            f"<b>Daily Expense:</b> <font color='red'>-{day_expense}</font>  |  "
            f"<b>Closing Balance:</b> {cl_text}"
        )
        elements.append(Spacer(1, 5))
        elements.append(Paragraph(daily_summary_text, normal_style))
        elements.append(Spacer(1, 15))

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

def generate_yearly_pdf_report(title, year, transactions):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = styles['Title']
    heading_style = styles['Heading2']
    normal_style = styles['Normal']

    # Title
    elements.append(Paragraph(title, title_style))
    elements.append(Paragraph(f"Year: {year}", normal_style))
    elements.append(Spacer(1, 20))

    # Calculate Summaries
    total_income = 0
    total_expense = 0
    
    # Monthly Breakdown
    monthly_data = defaultdict(lambda: {'income': 0, 'expense': 0})
    
    for txn in transactions:
        month_idx = txn.date.month
        # Income: Only INCOME type and NOT a repayment/linked to debt
        if txn.transaction_type == 'INCOME' and not txn.related_debt:
             monthly_data[month_idx]['income'] += txn.amount
             total_income += txn.amount
        # Expense: Only EXPENSE type and NOT a repayment/linked to debt
        elif txn.transaction_type == 'EXPENSE' and not txn.related_debt:
             monthly_data[month_idx]['expense'] += txn.amount
             total_expense += txn.amount
             
    net_savings = total_income - total_expense
    
    # Overall Summary
    elements.append(Paragraph("Annual Summary", heading_style))
    summary_data = [
        ['Total Income', f"+{total_income}"],
        ['Total Expense', f"-{total_expense}"],
        ['Net Savings', f"{net_savings}"]
    ]
    
    summary_table = Table(summary_data, colWidths=[200, 100])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (1, 0), colors.HexColor('#e6ffe6')), 
        ('BACKGROUND', (0, 1), (1, 1), colors.HexColor('#ffe6e6')), 
        ('BACKGROUND', (0, 2), (1, 2), colors.HexColor('#e6f7ff')), 
        ('TEXTCOLOR', (1, 0), (1, 0), colors.green),
        ('TEXTCOLOR', (1, 1), (1, 1), colors.red),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (1, 0), (1, 2), 'RIGHT'),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 25))

    # Monthly Breakdown Table
    elements.append(Paragraph("Monthly Breakdown", heading_style))
    
    table_header = ['Month', 'Income', 'Expense', 'Savings']
    table_data = [table_header]
    
    for m in range(1, 13):
        data = monthly_data[m]
        savings = data['income'] - data['expense']
        month_name = datetime.date(year, m, 1).strftime('%B')
        
        table_data.append([
            month_name,
            f"{data['income']}",
            f"{data['expense']}",
            f"{savings}"
        ])

    breakdown_table = Table(table_data, colWidths=[120, 100, 100, 100])
    
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
    ]
    
    # Row colors based on savings? Or just standard table
    # Let's color savings column
    for i in range(1, 13):
        savings = monthly_data[i]['income'] - monthly_data[i]['expense']
        color = colors.green if savings >= 0 else colors.red
        style_cmds.append(('TEXTCOLOR', (3, i), (3, i), color))
        
    breakdown_table.setStyle(TableStyle(style_cmds))
    elements.append(breakdown_table)
    
    
    # Category Breakdown (Annual)
    elements.append(Spacer(1, 25))
    elements.append(Paragraph("Top Expense Categories", heading_style))
    
    cat_data = defaultdict(int)
    for txn in transactions:
        if txn.transaction_type in ['EXPENSE', 'INVESTMENT']:
             cat_name = txn.category.name if txn.category else 'Uncategorized'
             cat_data[cat_name] += txn.amount
             
    sorted_cats = sorted(cat_data.items(), key=lambda x: x[1], reverse=True)
    
    cat_table_data = [['Category', 'Total Amount']]
    for name, amt in sorted_cats:
        cat_table_data.append([name, str(amt)])
        
    cat_table = Table(cat_table_data, colWidths=[200, 100])
    cat_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(cat_table)


    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

def generate_debt_pdf_report(debts):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = styles['Title']
    heading_style = styles['Heading2']
    normal_style = styles['Normal']

    # Title
    elements.append(Paragraph("Debt Report", title_style))
    elements.append(Paragraph(f"Generated on: {datetime.date.today().strftime('%d %B %Y')}", normal_style))
    elements.append(Spacer(1, 20))

    # Separate Debts and Sort: Pending First (is_cleared=False), then by Date Descending
    debts_taken = debts.filter(debt_type='TAKEN').order_by('is_cleared', '-date')
    debts_given = debts.filter(debt_type='GIVEN').order_by('is_cleared', '-date')

    def create_debt_table(debt_list, title, color_hex):
        if not debt_list:
            return
            
        elements.append(Paragraph(title, heading_style))
        
        # Calculate Total
        total_amount = sum(d.amount for d in debt_list if not d.is_cleared)
        elements.append(Paragraph(f"Total Outstanding: {total_amount}", normal_style))
        elements.append(Spacer(1, 10))

        table_data = [['Date', 'Person', 'Amount', 'Description', 'Status', 'Mode']]
        
        desc_style = ParagraphStyle(
            'DebtDesc',
            parent=normal_style,
            fontSize=8,
            leading=10
        )
        
        for d in debt_list:
            # Calculate total repaid
            repaid_amount = d.repayments.aggregate(Sum('amount'))['amount__sum'] or 0
            
            status_str = "Pending"
            if d.is_cleared:
                # Find the last repayment date
                last_repayment = d.repayments.order_by('-date').first()
                if last_repayment:
                    cleared_date = last_repayment.date.strftime('%Y-%m-%d')
                    status_str = f"Cleared ({cleared_date})"
                else:
                    status_str = "Cleared"
            elif repaid_amount > 0:
                remaining = d.amount - repaid_amount
                status_str = f"Partial (Paid: {repaid_amount}, Rem: {remaining})"

            desc_paragraph = Paragraph(d.description or "", desc_style)

            table_data.append([
                d.date.strftime('%Y-%m-%d'),
                d.person_name,
                f"{d.amount}",
                desc_paragraph,
                status_str,
                d.payment_mode
            ])
            
        t = Table(table_data, colWidths=[60, 90, 55, 145, 110, 40])
        
        style_cmds = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(color_hex)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (3, 1), (3, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]
        
        # Row coloring for status
        for i, d in enumerate(debt_list, start=1):
            if d.is_cleared:
                style_cmds.append(('TEXTCOLOR', (4, i), (4, i), colors.green))
            else:
                style_cmds.append(('TEXTCOLOR', (4, i), (4, i), colors.red))
                
        t.setStyle(TableStyle(style_cmds))
        elements.append(t)
        elements.append(Spacer(1, 20))

    # Debt Taken Table (Red theme header)
    create_debt_table(debts_taken, "Debts Taken (To Pay)", "#ff4d4d")

    # Debt Given Table (Green theme header)
    create_debt_table(debts_given, "Debts Given (To Collect)", "#4dff4d")

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

def generate_event_pdf_report(event_name, event_date_str, transactions):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = styles['Title']
    heading_style = styles['Heading2']
    normal_style = styles['Normal']
    
    # Custom styles
    desc_style = ParagraphStyle(
        'Desc',
        parent=normal_style,
        fontSize=9,
        leading=11
    )

    # Title
    elements.append(Paragraph(f"Event Report: {event_name}", title_style))
    elements.append(Paragraph(f"Date Created: {event_date_str}", normal_style))
    elements.append(Spacer(1, 20))

    # Calculate Summaries
    total_received = 0
    total_spent = 0
    
    for txn in transactions:
        if txn.transaction_type == 'INCOME' and not txn.related_debt:
            total_received += txn.amount
        elif txn.transaction_type == 'EXPENSE' and not txn.related_debt:
            total_spent += txn.amount
            
    balance = total_received - total_spent
    
    # Overall Summary
    elements.append(Paragraph("Event Summary", heading_style))
    summary_data = [
        ['Total Received', f"+{total_received}"],
        ['Total Spent', f"-{total_spent}"],
        ['Net Balance', f"{balance}"]
    ]
    
    summary_table = Table(summary_data, colWidths=[200, 100])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (1, 0), colors.HexColor('#e6ffe6')), 
        ('BACKGROUND', (0, 1), (1, 1), colors.HexColor('#ffe6e6')), 
        ('BACKGROUND', (0, 2), (1, 2), colors.HexColor('#e6f7ff')), 
        ('TEXTCOLOR', (1, 0), (1, 0), colors.green),
        ('TEXTCOLOR', (1, 1), (1, 1), colors.red),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (1, 0), (1, 2), 'RIGHT'),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 25))

    # Transactions List
    elements.append(Paragraph("Transactions", heading_style))
    
    if not transactions:
        elements.append(Paragraph("No transactions found for this event.", normal_style))
    else:
        txn_data = [['Date', 'Description', 'Type', 'Amount', 'Mode']]
        
        for txn in transactions:
            desc_para = Paragraph(txn.description, desc_style)
            txn_data.append([
                txn.date.strftime('%Y-%m-%d'),
                desc_para,
                txn.transaction_type.replace('_', ' '),
                txn.amount,
                txn.payment_mode
            ])
            
        t = Table(txn_data, colWidths=[70, 210, 80, 70, 60])
        
        style_cmds = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (1, 1), (1, -1), 'LEFT'), # Description align left
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
        ]
        
        # Apply row colors
        for i, txn in enumerate(transactions, start=1):
             color = colors.green if txn.transaction_type in ['INCOME', 'DEBT_TAKEN', 'CASH_DEPOSIT', 'DEBT_GIVEN_RETURN', 'FUND_MANAGEMENT_INC'] else colors.red
             style_cmds.append(('TEXTCOLOR', (3, i), (3, i), color))
             
        t.setStyle(TableStyle(style_cmds))
        elements.append(t)
        
    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

