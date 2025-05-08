"""add_video_sticker_fields

Revision ID: 75775e318009
Revises: add_document_data_column
Create Date: 2025-05-08 16:34:56.130944

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '75775e318009'
down_revision: Union[str, None] = 'add_document_data_column'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
