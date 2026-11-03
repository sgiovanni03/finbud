import os
from os.path import join, dirname
from dotenv import load_dotenv
from sqlalchemy import create_engine, select, String
import urllib
import pandas as pd
from sqlalchemy.orm import DeclarativeBase, Session
# from typing import List, Optional, String
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

dotenv_path = join(dirname(__file__), '.env')
load_dotenv(dotenv_path)
    
connection_string = os.environ["AZURE_SQL_CONNECTIONSTRING"]

class Base(DeclarativeBase):
    pass

class accounts(Base):
    __tablename__ = 'accounts'

    account_number: Mapped [int] = mapped_column(primary_key=True)
    account_name: Mapped[str] = mapped_column(String(30))
    account_type: Mapped[str] = mapped_column(String(30))

    def __repr__(self) -> str:
        return f"accounts(account_number={self.account_number!r}, account_name={self.account_name!r}, account_type={self.account_type!r})"


# params = urllib.parse.quote_plus(connection_string)
access_str = urllib.parse.quote_plus('DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=C:/Users/sgiov/OneDrive/Documents/finbud.accdb')
# conn_str = 'mssql+pyodbc:///?odbc_connect={}'.format(params)
conn_str = 'access+pyodbc:///?odbc_connect={}'.format(access_str)
engine = create_engine(conn_str,echo=True)

session = Session(engine)

stmt = select(accounts)

for account in session.scalars(stmt):
    print(account)

# query = 'SELECT * FROM accounts'

# with engine_azure.connect() as connection:
#     df = pd.read_sql_query(sql=text(query), con=connection)
# print(df.to_markdown())
