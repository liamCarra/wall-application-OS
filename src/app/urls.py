from django.contrib import admin
from django.urls import path
from .views import homepage, about, gallery, signin, signup, logout_view, serve_profile_picture, get_profile_picture, profile, delete_favorite_image, upload_image, generate_image, support, thread_detail, create_thread, change_thread_status, delete_thread, create_checkout_session, stripe_webhook

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', homepage, name='homepage'),
    path('about/', about, name='about'),
    path('gallery/', gallery, name='gallery'),
    path('signin/', signin, name='signin'),
    path('signup/', signup, name='signup'),
    path('logout/', logout_view, name='logout'),
    path('profile_picture/<int:user_id>/', serve_profile_picture, name='serve_profile_picture'),
    path('profile-picture/<str:username>/', get_profile_picture, name='get_profile_picture'),
    path('profile/', profile, name='profile'),
    path('profile/<str:username>/', profile, name='profile_other'),
    path('upload_image/', upload_image, name='upload_image'),
    path('delete_favorite_image/', delete_favorite_image, name='delete_favorite_image'),
    path('generate_image/', generate_image, name='generate_image'),
    path('support/', support, name='support'),
    path('support/thread/<int:thread_id>/', thread_detail, name='thread_detail'),
    path('support/create/', create_thread, name='create_thread'),
    path('support/thread/<int:thread_id>/status/<str:new_status>/', change_thread_status, name='change_thread_status'),
    path('support/thread/<int:thread_id>/delete/', delete_thread, name='delete_thread'),
    # Stripe billing
    path('billing/checkout', create_checkout_session, name='create_checkout_session'),
    path('stripe/webhook', stripe_webhook, name='stripe_webhook'),
]
