from django.urls import path

from ..views import study_sets

app_name = 'study_sets'

urlpatterns = [
    path('create/', study_sets.study_set_create, name='create'),
    path('<int:pk>/edit/', study_sets.study_set_edit, name='edit'),
    path('<int:pk>/delete/', study_sets.study_set_delete, name='delete'),
    path('<int:pk>/pin/', study_sets.study_set_toggle_pin, name='toggle_pin'),
    path('decks/<int:deck_id>/toggle/', study_sets.study_set_toggle_deck, name='toggle_deck'),
]
