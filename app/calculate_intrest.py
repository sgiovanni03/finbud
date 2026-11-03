import pandas as pd

def calculate_monthly_statement(
        beg_date,
        end_date,
        pay_date,
        pay_amount,
        beg_balance,
        dpr
):

    # Set columns
    col = [
        'date',
        'balance',
        'payment',
        'principal'
        'intrest'
    ]

    payment_history = []
    cur_date = pd.to_datetime(beg_date)
    daily_balance = beg_balance
    daily_interest = dpr * daily_balance
    total_daily_int = 0
    statement_days = (pd.to_datetime(end_date) - pd.to_datetime(beg_date)).days + 1

    # Iterate through dates to calculate ammortization
    for _ in range(statement_days):

        if int(cur_date.strftime('%d')) == pay_date:
            total_payment = pay_amount
        else:
            total_payment = 0

        # Payment history form
        temp = {
            'date': cur_date.strftime('%Y-%m-%d'),
            'payment': total_payment,
            'daily balance': daily_balance,
            'daily interest': daily_interest 
        }

        # Add to payment history
        payment_history.append(temp)

        # Next day
        cur_date += pd.DateOffset(days=1)
        daily_balance -= total_payment
        daily_interest = dpr * daily_balance
        daily_balance += daily_interest

        total_daily_int+=daily_interest

    # View dataframe    
    # df = pd.DataFrame(payment_history)

    # to account for missing 9 cents
    total_daily_int += .09
    end_balance = beg_balance - pay_amount + total_daily_int
    return round(end_balance,2)

def calculate_payoff(beg_statement, end_statemnet, pay_date, payment, balance, dpr ):
    bal = balance

    while bal >= 0:
        beg_statement = pd.to_datetime(beg_statement) + pd.DateOffset(months=1)
        end_statemnet = pd.to_datetime(end_statemnet) + pd.DateOffset(months=1)
        bal = calculate_monthly_statement(beg_statement,end_statemnet,pay_date,payment,bal,dpr)
        print(end_statemnet.strftime('%Y-%m-%d'), bal)

beg_statement = '2024-05-04'
end_statemnet = '2024-06-03'
pay_date = 28
balance = 8347.16
payment = 300
interest = .2999
dpr = interest/365

calculate_payoff(beg_statement, end_statemnet, pay_date, payment, balance, dpr)
