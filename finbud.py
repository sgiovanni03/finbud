import sqlite3
import datetime
from datetime import *
from dateutil.relativedelta import relativedelta
from sqlite3 import Error

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import style
style.use('fivethirtyeight')##Use any style you like

from dateutil import parser
# from w1thermsensor import W1ThermSensor

TRANSACTION_ID = 0

def create_connection(db_file):
    """ create a database connection to the SQLite database
        specified by db_file
    :param db_file: database file
    :return: Connection object or None
    """
    conn = None
    try:
        conn = sqlite3.connect(db_file)
    except Error as e:
        print(e)

    return conn


def create_account(conn, account):
    """
    Create a new account
    :param conn:
    :param account:
    :return:
    """

    sql = ''' INSERT INTO accounts(account_id,account_name) VALUES(?,?) '''
    cur = conn.cursor()
    cur.execute(sql,account)
    conn.commit()
    return cur.lastrowid

def create_transaction(conn, transaction):
    """
    Create a new account
    :param conn:
    :param transaction:
    :return:
    """

    sql = ''' INSERT INTO transactions(transaction_date,transaction_amount,account_id) VALUES(?,?,?) '''
    cur = conn.cursor()
    cur.execute(sql,transaction)
    conn.commit()
    return cur.lastrowid

def calculate_repayment(start_date, account_id, balance, min, exp_date,conn):
    month_diff = (exp_date - start_date).days//30
    
    for month in range(month_diff):
        create_transaction(conn,(start_date.strftime("%Y-%m-%d"),round(min,2),account_id))
        balance-=min
        start_date = start_date + relativedelta(months = 1)
    create_transaction(conn,(start_date.strftime("%Y-%m-%d"),round(balance,2),account_id))


def calculate_repayment_equal(start_date, account_id, min, exp_date,conn):
    month_diff = (exp_date - start_date).days//30
    
    for month in range(month_diff+1):
        create_transaction(conn,(start_date.strftime("%Y-%m-%d"),round(min,2),account_id))
        start_date = start_date + relativedelta(months = 1)

def graph_data(conn):
    c = conn.cursor()

    date = ["2022-03","2022-04","2022-05","2022-06","2022-07","2022-08","2022-09","2022-10","2022-11","2022-12"]
    balance = []
    month = 3
    startdate = '"2022-' + str(month).zfill(2) + '-01"'
    enddate = '"2022-' + str(month).zfill(2) + '-31"'

    for m in range(month,13):
        c.execute(f'SELECT SUM(transaction_amount) FROM transactions WHERE transaction_date BETWEEN {startdate} AND {enddate};')
        data = c.fetchall()[0][0]
        balance.append(round(data,2))

        month+=1
        startdate = '"2022-' + str(month).zfill(2) + '-01"'
        enddate = '"2022-' + str(month).zfill(2) + '-31"'

    for i,d in enumerate(date):
        diff = balance[i]
        print(f'{d}: {diff}')
        
    
    # plt.plot_date(date,balance,'-')
    # plt.bar(date, balance, color = 'blue', width=.4)
    # plt.xlabel("Monthly")
    # plt.ylabel("Net Income")
    # plt.show()


def main():
    database = r"finbud.db"

    # create a database connection
    conn = create_connection(database)
    
    '''
    paydate = date(2022, 3, 4)
    enddate = date(2022, 12, 31)

    while True:
        paydate = paydate+timedelta(days=14)
        if paydate > enddate: break
        create_transaction(conn,(paydate.strftime("%Y-%m-%d"),3600,403))
        '''
    
    ''' Repayment Block '''
    startdate = date(2022,4,1)
    account_id = 166
    balance = -55263.04
    minimum_payment = 40.13
    exp_date = date(2024,12,1)

    # calculate_repayment(startdate, account_id,balance,minimum_payment,exp_date,conn)
    calculate_repayment_equal(startdate, account_id,minimum_payment,exp_date,conn)

    ''' Graphing Block '''
    graph_data(conn)   
        
if __name__ == '__main__':
    main()
    