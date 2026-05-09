from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from core.views import home
from api import views as api_views

urlpatterns = [
    path('', home.landing, name='landing'),
    path('admin/', admin.site.urls),
    path('accounts/', include(('accounts.urls', 'accounts'), namespace='accounts')),
    path('decks/', include(('core.urls.decks', 'decks'), namespace='decks')),
    path('cards/', include(('core.urls.cards', 'cards'), namespace='cards')),
    path('study-sets/', include(('core.urls.study_sets', 'study_sets'), namespace='study_sets')),
    path('knowledge-maps/', include(('core.urls.knowledge_maps', 'knowledge_maps'), namespace='knowledge_maps')),
    path('review/', include(('core.urls.review', 'review'), namespace='review')),
    path('settings/', include(('core.urls.settings', 'settings'), namespace='settings')),
    path('imports/', include(('import_md.urls', 'imports'), namespace='imports')),
    path('api/v1/', include(('api.urls', 'api'), namespace='api')),
    path('api/knowledge-maps/', api_views.knowledge_maps_collection, name='legacy-knowledge-maps'),
    path('api/knowledge-maps/import', api_views.knowledge_map_import, name='legacy-knowledge-map-import'),
    path('api/knowledge-maps/<slug:map_slug>', api_views.knowledge_map_detail, name='legacy-knowledge-map-detail'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
