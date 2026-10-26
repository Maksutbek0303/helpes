from .permissions import is_admin, is_technician


def roles(request):
    user = request.user
    return {"user_is_admin": is_admin(user), "user_is_tech": is_technician(user)}
