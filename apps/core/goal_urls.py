from django.urls import path

from . import views

app_name = "goals"

urlpatterns = [
    path("", views.GoalListView.as_view(), name="goal_list"),
    path("create/", views.GoalCreateView.as_view(), name="goal_create"),
    path("<int:pk>/edit/", views.GoalUpdateView.as_view(), name="goal_edit"),
    path("<int:pk>/main/", views.GoalMainView.as_view(), name="goal_main"),
    path("<int:pk>/delete/", views.GoalDeleteView.as_view(), name="goal_delete"),
]
