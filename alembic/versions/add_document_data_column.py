"""add document_data column

Revision ID: add_document_data_column
Revises: add_voice_data_column
Create Date: 2025-05-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'add_document_data_column'
down_revision: Union[str, None] = 'add_voice_data_column'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Add document_data column to message_history table
    op.add_column('message_history',
                  sa.Column('document_data', sa.LargeBinary(), nullable=True))

def downgrade() -> None:
    # Remove document_data column from message_history table 
    op.drop_column('message_history', 'document_data')