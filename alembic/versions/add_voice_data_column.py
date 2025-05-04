"""add_voice_data_column

Revision ID: add_voice_data_column
Revises: 6ffc464705e0
Create Date: 2025-05-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'add_voice_data_column'
down_revision: Union[str, None] = '6ffc464705e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Rename audio_data to voice_data
    with op.batch_alter_table('message_history') as batch_op:
        batch_op.alter_column('audio_data',
                            new_column_name='voice_data',
                            existing_type=sa.LargeBinary(),
                            nullable=True)

def downgrade() -> None:
    # Rename voice_data back to audio_data
    with op.batch_alter_table('message_history') as batch_op:
        batch_op.alter_column('voice_data',
                            new_column_name='audio_data',
                            existing_type=sa.LargeBinary(),
                            nullable=True)