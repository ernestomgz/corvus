import factory

from accounts.models import User
from core.models import Card, Deck, ExternalId, SchedulingState
from core.scheduling import ensure_state


class UserFactory(factory.django.DjangoModelFactory):
    email = factory.Sequence(lambda n: f'user{n}@example.com')
    password = factory.PostGenerationMethodCall('set_password', 'password123')

    class Meta:
        model = User


class DeckFactory(factory.django.DjangoModelFactory):
    user = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f'Deck {n}')
    description = ''

    class Meta:
        model = Deck


class CardFactory(factory.django.DjangoModelFactory):
    user = factory.SubFactory(UserFactory)
    deck = factory.SubFactory(DeckFactory, user=factory.SelfAttribute('..user'))
    front_md = factory.Sequence(lambda n: f'Front {n}')
    back_md = factory.Sequence(lambda n: f'Back {n}')
    tags = factory.LazyFunction(list)

    class Meta:
        model = Card

    @factory.post_generation
    def ensure_schedule(self, create, extracted, **kwargs):  # pragma: no cover
        if create:
            ensure_state(self)


class SchedulingStateFactory(factory.django.DjangoModelFactory):
    card = factory.SubFactory(CardFactory)
    ease = 2.5
    interval_days = 0
    reps = 0
    lapses = 0
    queue_status = 'new'
    due_at = None
    learning_step_index = 0
    last_rating = None

    class Meta:
        model = SchedulingState


class ExternalIdFactory(factory.django.DjangoModelFactory):
    card = factory.SubFactory(CardFactory)
    system = 'logseq'
    external_key = factory.Sequence(lambda n: f'ext-{n}')

    class Meta:
        model = ExternalId


class StudySetFactory(factory.django.DjangoModelFactory):
    user = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f'Study Set {n}')
    kind = 'custom'
    is_favorite = False

    class Meta:
        model = 'core.StudySet'
