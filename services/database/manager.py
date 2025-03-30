from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
import asyncpg 

from .models import Base


class DatabaseManager:
    def __init__(self, user: str, password: str, host: str, db_name: str) -> None:
        self.user = user
        self.password = password
        self.host = host
        self.db_name = db_name
        self.async_url = f"postgresql+asyncpg://{user}:{password}@{host}/{db_name}"
        self.base = Base
        self.engine = create_async_engine(self.async_url, echo=True)
        self.async_session_maker = async_sessionmaker(
            bind=self.engine, expire_on_commit=False, class_=AsyncSession
        )

    async def create_database(self):
        """Creates the database asynchronously if it doesn't exist."""
        try:
            # Connect to the default 'postgres' database to create the new one
            conn = await asyncpg.connect(
                user=self.user, password=self.password, host=self.host, database="postgres"
            )
            try:
                await conn.execute(f"CREATE DATABASE {self.db_name}")
                print(f"Database '{self.db_name}' created successfully.")
            except asyncpg.DuplicateDatabaseError:
                print(f"Database '{self.db_name}' already exists.")
            finally:
                await conn.close()
        except Exception as e:
            print(f"Error creating database: {e}")


    async def create_tables(self) -> None:
        """Creates tables in the database."""
        async with self.engine.begin() as conn:
            await conn.run_sync(self.base.metadata.create_all)

    def session(self):
        return self.async_session_maker