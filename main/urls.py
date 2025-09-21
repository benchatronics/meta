from django.urls import path
from . import views
from .views import support_reset_password
from . import user_taskview as task_views
from . import admin_view as bo

urlpatterns = [
    path('homeindexhomeindexhome', views.index, name='index'),
    path('accounts/login/', views.signin, name='signin'),
    path('', views.signup_view, name='signup'),
    path('signout/', views.signout, name='signout'),
    #rewards
    path("rewards/", views.rewards, name="rewards"),
    #NEW: password reset by phone
    path("settings_pass_reset/", views.settings_change_password, name="setting_change_password"),
    path('reset/', views.password_reset_start, name='password_reset_start'),
    path('reset/verify/', views.password_reset_verify, name='password_reset_verify'),
    path("support/reset-password/", support_reset_password, name="support_reset_password"),
    path("user_dashboard/", views.user_dashboard, name="user_dashboard"),
    path("favorite/<slug:slug>/", views.toggle_favorite, name="toggle_favorite"),
    #info
    path("info/", views.info_index, name="info_index"),
    path("info/<slug:key>/", views.info_page, name="info_page"),
    path("announcements/", views.announcements_list, name="announcements"),
    path("user_dashboard/wallet_view", views.wallet_view, name="wallet"),
    # Withdraw
    path("wallet/user_withdrawal", views.withdrawal, name="withdrawal"),
    path("withdraw/address/add/", views.add_address, name="withdraw_add_address"),
    path("withdraw/success/", views.withdrawal_success, name="withdrawal_success"),
    # Deposit
    path("deposit/", views.deposit, name="deposit"),
    path("deposit/pay/<int:pk>/", views.deposit_pay, name="deposit_pay"),
    path("deposit/verify/<int:pk>/", views.deposit_verify, name="deposit_verify"),
    path("deposit/<int:pk>/status/", views.deposit_status, name="deposit_status"),  # <- JSON status
    #for auto confirmation
    path("deposit/admin-confirm/<int:pk>/", views.deposit_admin_confirm, name="deposit_admin_confirm"),
    path("deposit/webhook/confirm/", views.deposit_webhook_confirm, name="deposit_webhook_confirm"),
    path("language_settings/", views.language_settings, name="language_setting"),
    path("settings/", views.profile_settings, name="profile_settings"),
    #withdrawal password
    path("settings/tx-pin/set/", views.set_tx_pin, name="set_tx_pin"),
    path("settings/tx-pin/change/", views.change_tx_pin, name="change_tx_pin"),
    #user task
    path("tasks/", task_views.task_dashboard, name="task_dashboard"),
    path("tasks/do/", task_views.do_task, name="do_task"),          # single link to do task
    path("tasks/<int:pk>/", task_views.task_detail, name="task_detail"),

    #Admin workshop
    path("bo/", bo.bo_dashboard, name="admin_dashboard"),
    path("bo/withdrawals/", bo.bo_withdrawals, name="bo_withdrawals"),
    path("bo/withdrawals/<int:pk>/approve/", bo.bo_withdrawal_approve, name="bo_withdrawal_approve"),
    path("bo/withdrawals/<int:pk>/fail/", bo.bo_withdrawal_fail, name="bo_withdrawal_fail"),

    path("bo/deposits/", bo.bo_deposits, name="bo_deposits"),
    path("bo/deposits/<int:pk>/review/", bo.bo_deposit_move_to_review, name="bo_deposit_move_to_review"),
    path("bo/deposits/<int:pk>/confirm/", bo.bo_deposit_confirm, name="bo_deposit_confirm"),
    path("bo/deposits/<int:pk>/fail/", bo.bo_deposit_fail, name="bo_deposit_fail"),

    path("bo/users/", bo.bo_users, name="bo_users"),
    path("bo/users/<int:user_id>/", bo.bo_user_detail, name="bo_user_detail"),
    path("bo/users/<int:user_id>/wallet/credit/", bo.bo_user_wallet_credit, name="bo_user_wallet_credit"),
    path("bo/users/<int:user_id>/wallet/debit/", bo.bo_user_wallet_debit, name="bo_user_wallet_debit"),
    path("bo/users/<int:user_id>/unblock/", bo.bo_user_unblock, name="bo_user_unblock"),
    path("bo/users/<int:user_id>/clear-txpin/", bo.bo_user_clear_txpin, name="bo_user_clear_txpin"),
    path("bo/users/<int:user_id>/wallet/txns/", bo.bo_wallet_txns, name="bo_wallet_txns"),
    path("bo/users/<int:user_id>/payout-addresses/", bo.bo_payout_addresses, name="bo_payout_addresses"),

    path("bo/settings/", bo.bo_settings, name="bo_settings"),
    path("bo/templates/", bo.bo_templates, name="bo_templates"),
    path("bo/templates/<int:tpl_id>/status/", bo.bo_template_toggle_status, name="bo_template_toggle_status"),
    path("bo/directives/", bo.bo_directives, name="bo_directives"),
    path("bo/directives/create/", bo.bo_directive_create, name="bo_directive_create"),
    path("bo/directives/<int:dir_id>/cancel/", bo.bo_directive_cancel, name="bo_directive_cancel"),
    path("bo/tasks/", bo.bo_tasks, name="bo_tasks"),
    path("bo/tasks/<int:task_id>/approve-admin/", bo.bo_task_approve_admin, name="bo_task_approve_admin"),
    path("bo/tasks/<int:task_id>/reject/", bo.bo_task_reject, name="bo_task_reject"),

    path("bo/info-pages/", bo.bo_info_pages, name="bo_info_pages"),
    path("bo/info-pages/<int:pk>/", bo.bo_info_page_edit, name="bo_info_page_edit"),
    path("bo/announcements/", bo.bo_announcements, name="bo_announcements"),
    path("bo/announcements/new/", bo.bo_announcement_edit, name="bo_announcement_new"),
    path("bo/announcements/<int:pk>/", bo.bo_announcement_edit, name="bo_announcement_edit"),
]
