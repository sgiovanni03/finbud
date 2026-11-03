import urllib
from os.path import join, dirname
from dotenv import load_dotenv
from sqlalchemy import create_engine, select, String, Date, Integer, Sequence, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.orm import Mapped, mapped_column

dotenv_path = join(dirname(__file__), '.env')
load_dotenv(dotenv_path)


class Base(DeclarativeBase):
    pass


# Accounts Model
class Accounts(Base):
    __tablename__ = 'accounts'

    account_number: Mapped[int] = mapped_column(primary_key=True)
    account_name: Mapped[str] = mapped_column(String(30))
    account_type: Mapped[str] = mapped_column(String(30))

    def __repr__(self) -> str:
        return f"accounts(account_number={self.account_number!r}, \
            account_name={self.account_name!r}, \
                account_type={self.account_type!r})"


# Transactions Model
class Transactions(Base):
    __tablename__ = 'transactions'

    id: Mapped[int] = mapped_column(Integer, Sequence('%s_id_seq' % __tablename__), primary_key=True)
    account_number: Mapped[int] = mapped_column(Integer)
    account_balance: Mapped[str] = mapped_column(String(30))
    date: Mapped[str] = mapped_column(Date)

    __table_args__ = (UniqueConstraint("account_number", "account_balance", "date", name="three_columns"),)

    def __repr__(self) -> str:
        return f"transactions(id={self.id!r}, \
            account_number={self.account_number!r}, \
            account_balance={self.account_balance!r}, \
                date={self.date!r})"


TABLES = {
    "accounts": Accounts,
    "transactions": Transactions
}


class db_connect:

    def __init__(self) -> None:
        access_str = urllib.parse.quote_plus(
            'DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};\
                DBQ=C:/Users/sgiov/OneDrive/Documents/finbud.accdb')
        conn_str = 'access+pyodbc:///?odbc_connect={}'.format(access_str)
        self.engine = create_engine(conn_str, echo=False)

        Base.metadata.create_all(self.engine)

        self.session = Session(self.engine)

    def get_table(self, table):

        stmt = select(TABLES.get(table))

        for record in self.session.scalars(stmt):
            print(record)

    def set_account(self, data):
        try:
            return Accounts(
                account_number=data.get("account_number"),
                account_name=data.get("account_name"),
                account_type=data.get("account_type"),
            )
        except Exception:
            print("There was an error setting Account:")

    def set_transactions(self, data):
        try:
            return Transactions(
                account_number=data.get("account_number"),
                account_balance=data.get("account_balance"),
                date=data.get("date"),
            )
        except Exception:
            print("There was an error setting Transactions:")

    def create_record(self, records_dump):
        records = []

        # Parse/Create records
        for record in records_dump:
            if record.get("table") == "accounts":
                tmp_rec = self.set_account(record.get("data"))
            elif record.get("table") == "transactions":
                tmp_rec = self.set_transactions(record.get("data"))

            records.append(tmp_rec)

        with self.session as session:
            session.add_all(records)

            session.commit()

    def delete_account(self, account_number):
        with self.session as session:
            tmp_account = session.get(Accounts, account_number)

            session.delete(tmp_account)

            session.commit()
