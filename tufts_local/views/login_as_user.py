from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth import get_user_model
from django.http import HttpResponseBadRequest
from django.contrib.auth.decorators import user_passes_test
from django_su.views import login_as_user
from django_su.utils import su_login_callback


@csrf_protect
@require_POST
@user_passes_test(su_login_callback)
def login_as_user_view(request, username):
    """
    View to allow superusers to log in as another user.
    """
    user_ids = get_user_model().objects.filter(username=username).values_list('id', flat=True)
    if len(user_ids) > 1:
        raise HttpResponseBadRequest("Multiple users found with the same username.")
    if not user_ids:
        raise HttpResponseBadRequest("No user found with the provided username.")
    user_id = user_ids[0]
    return login_as_user(request, user_id)

