from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from core.models import Card, Deck
from core.scheduling import ensure_state


class Command(BaseCommand):
    help = 'Create a demo user, deck, and starter cards.'

    def handle(self, *args, **options):
        user, created = User.objects.get_or_create(
            email='demo@example.com',
            defaults={'created_at': timezone.now()},
        )
        if created or not user.password:
            user.set_password('demo1234')
            user.is_staff = True
            user.save()
            self.stdout.write(self.style.SUCCESS('Created demo user demo@example.com / demo1234'))
        else:
            self.stdout.write('Demo user already exists.')

        deck, _ = Deck.objects.get_or_create(user=user, name='Demo Deck', defaults={'description': 'Sample cards'})
        samples = [
            ('What is the capital of France?', 'Paris'),
            ('{{c1::Python}} is a {{c2::dynamic}} language.', 'Fill in the blanks to remember details.'),
            ('Problem: 3 * 7', 'Answer: 21'),
        ]
        created_cards = 0
        for front, back in samples:
            card, created_card = Card.objects.get_or_create(
                user=user,
                deck=deck,
                front_md=front,
                defaults={'back_md': back},
            )
            ensure_state(card)
            if created_card:
                created_cards += 1
        self.stdout.write(self.style.SUCCESS(f'Seeded deck with {created_cards} new cards.'))
