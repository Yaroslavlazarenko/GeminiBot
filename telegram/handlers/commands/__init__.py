from .clear import router as clear_router
from .group_settings import router as group_settings_router
from .user_settings import router as user_settings_router

command_routers = [
    clear_router,
    group_settings_router,
    user_settings_router,
]