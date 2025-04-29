from .clear import router as clear_router
from .inline_menu import router as inline_menu_router
from .group_inline_menu import router as group_inline_menu_router
from .help import router as help_router

command_routers = [
    clear_router,
    inline_menu_router,
    group_inline_menu_router,
    help_router,
]