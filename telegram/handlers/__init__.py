from .commands import command_routers
from .messages import message_routers

all_routers = (
    command_routers
    + message_routers
)