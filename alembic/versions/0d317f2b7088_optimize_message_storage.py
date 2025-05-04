"""optimize_message_storage

Revision ID: 0d317f2b7088
Revises: add_sticker_response
Create Date: 2024-05-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0d317f2b7088'
down_revision: Union[str, None] = 'add_sticker_response'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Создаем новый тип перечисления если его еще нет
    messagerole = postgresql.ENUM('USER', 'MODEL', name='messagerole')
    messagerole.create(op.get_bind(), checkfirst=True)
    
    # Обновляем тип колонки role
    with op.batch_alter_table('message_history', schema=None) as batch_op:
        batch_op.alter_column('role',
                    existing_type=sa.VARCHAR(length=5),
                    type_=sa.Enum('USER', 'MODEL', name='messagerole'),
                    existing_nullable=False,
                    postgresql_using="role::text::messagerole")

def downgrade() -> None:
    # Возвращаем тип колонки role к VARCHAR
    with op.batch_alter_table('message_history', schema=None) as batch_op:
        batch_op.alter_column('role',
                    type_=sa.VARCHAR(length=5),
                    existing_type=sa.Enum('USER', 'MODEL', name='messagerole'),
                    existing_nullable=False)

    # Удаляем тип перечисления
    messagerole = postgresql.ENUM('USER', 'MODEL', name='messagerole')
    messagerole.drop(op.get_bind())
