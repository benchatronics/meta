from django.urls import path
from . import views
from .views import support_reset_password


urlpatterns = [
    path('', views.index, name='index'),
    path('accounts/login/', views.signin, name='signin'),
    path('signup/', views.signup_view, name='signup'),
    path('signout/', views.signout, name='signout'),
    #NEW: password reset by phone
    path('reset/', views.password_reset_start, name='password_reset_start'),
    path('reset/verify/', views.password_reset_verify, name='password_reset_verify'),
    path("support/reset-password/", support_reset_password, name="support_reset_password"),
    path("user_dashboard/", views.user_dashboard, name="user_dashboard"),
    path("favorite/<slug:slug>/", views.toggle_favorite, name="toggle_favorite"),

    path("user_dashboard/wallet_view", views.wallet_view, name="wallet"),
    # Withdraw
    path("wallet/user_withdrawal", views.withdrawal, name="withdrawal"),
    path("withdraw/address/add/", views.add_address, name="withdraw_add_address"),
    path("withdraw/success/", views.withdrawal_success, name="withdrawal_success"),
    # Deposit
    path("deposit/", views.deposit, name="deposit"),
    path("deposit/pay/<int:pk>/", views.deposit_pay, name="deposit_pay"),
    path("deposit/verify/<int:pk>/", views.deposit_verify, name="deposit_verify"),
    #for auto confirmation
    path("deposit/admin-confirm/<int:pk>/", views.deposit_admin_confirm, name="deposit_admin_confirm"),
    path("deposit/webhook/confirm/", views.deposit_webhook_confirm, name="deposit_webhook_confirm"),
]