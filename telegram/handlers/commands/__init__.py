from .clear import router as clear_router
from .group_settings import router as group_settings_router
from .inline_menu import router as inline_menu_router

command_routers = [
    clear_router,
    group_settings_router,
    inline_menu_router,
]