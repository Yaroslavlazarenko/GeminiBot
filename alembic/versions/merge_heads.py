"""merge heads

Revision ID: merge_heads_20240423
Revises: 20240320_add_video_note, 205fb5acb24f
Create Date: 2024-04-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'merge_heads_20240423'
down_revision: Union[str, None] = ('20240320_add_video_note', '205fb5acb24f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass 