"""add_stickers_table_and_relations

Revision ID: add_stickers_table
Revises: cdebc0646660
Create Date: 2025-05-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_stickers_table'
down_revision = 'cdebc0646660'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create stickers table
    op.create_table('stickers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('telegram_sticker_id', sa.String(length=256), nullable=False),
        sa.Column('telegram_message_id', sa.BigInteger(), nullable=True),
        sa.Column('name', sa.String(length=256), nullable=True),
        sa.Column('emoji', sa.String(length=32), nullable=True),
        sa.Column('image_data', sa.LargeBinary(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_stickers_telegram_sticker_id'), 'stickers', ['telegram_sticker_id'], unique=False)
    op.create_index(op.f('ix_stickers_telegram_message_id'), 'stickers', ['telegram_message_id'], unique=False)

    # Add sticker_id to message_history
    op.add_column('message_history',
        sa.Column('sticker_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_message_history_sticker_id_stickers',
        'message_history', 'stickers',
        ['sticker_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Remove foreign key and sticker_id column from message_history
    op.drop_constraint('fk_message_history_sticker_id_stickers', 'message_history', type_='foreignkey')
    op.drop_column('message_history', 'sticker_id')

    # Drop stickers table and indexes
    op.drop_index(op.f('ix_stickers_telegram_message_id'), table_name='stickers')
    op.drop_index(op.f('ix_stickers_telegram_sticker_id'), table_name='stickers')
    op.drop_table('stickers')