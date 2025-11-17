from sqlalchemy import Table, Column, MetaData, select, insert, update, delete, text
from sqlalchemy.orm import sessionmaker

from src.data.database.db_engine import engine, SessionLocal

metadata = MetaData()

class TableManager:
    def __init__(self):
        self.Session = SessionLocal
        self.tables_cache = {}

    def create_table(self, schema_name: str, table_name: str, columns: list):
        table = Table(table_name, metadata, *columns, schema=schema_name)
        table.create(bind=engine, checkfirst=True)
        key = f"{schema_name}.{table_name}"
        self.tables_cache[key] = table

    def drop_table(self, schema_name: str, table_name: str):
        table = Table(table_name, metadata, schema=schema_name)
        table.drop(bind=engine, checkfirst=True)
        key = f"{schema_name}.{table_name}"
        if key in self.tables_cache:
            del self.tables_cache[key]

    def get_table(self, schema_name: str, table_name: str) -> Table:
        key = f"{schema_name}.{table_name}"
        if key not in self.tables_cache:
            self.tables_cache[key] = Table(table_name, metadata, autoload_with=engine, schema=schema_name)
        return self.tables_cache[key]

    def run_query(self, query: str):
        with self.Session() as session:
            result = session.execute(text(query))
            return [dict(row._mapping) for row in result]

    def insert_row(self, schema_name: str, table_name: str, data: dict):
        table = self.get_table(schema_name, table_name)
        stmt = insert(table).values(**data)
        with self.Session() as session:
            session.execute(stmt)
            session.commit()

    def insert_many(self, schema_name: str, table_name: str, rows: list[dict]):
        if not rows:
            return
        table = self.get_table(schema_name, table_name)
        stmt = insert(table)
        with self.Session() as session:
            session.execute(stmt, rows)
            session.commit()

    def fetch_all_rows(self, schema_name: str, table_name: str):
        table = self.get_table(schema_name, table_name)
        stmt = select(table)
        with self.Session() as session:
            result = session.execute(stmt)
            return [dict(row._mapping) for row in result]

    def fetch_rows(self, schema_name: str, table_name: str, filters: dict = None):
        table = self.get_table(schema_name, table_name)
        stmt = select(table)
        if filters:
            for k, v in filters.items():
                stmt = stmt.where(table.c[k] == v)
        with self.Session() as session:
            result = session.execute(stmt)
            return [dict(row._mapping) for row in result]

    def update_rows(self, schema_name: str, table_name: str, data: dict, where: dict):
        if not where:
            raise ValueError("update_rows requires WHERE conditions")
        table = self.get_table(schema_name, table_name)
        stmt = update(table)
        for k, v in where.items():
            stmt = stmt.where(table.c[k] == v)
        stmt = stmt.values(**data)
        with self.Session() as session:
            session.execute(stmt)
            session.commit()

    def delete_rows(self, schema_name: str, table_name: str, where: dict):
        if not where:
            raise ValueError("delete_rows requires WHERE conditions")
        table = self.get_table(schema_name, table_name)
        stmt = delete(table)
        for k, v in where.items():
            stmt = stmt.where(table.c[k] == v)
        with self.Session() as session:
            result = session.execute(stmt)
            session.commit()
            return result.rowcount
