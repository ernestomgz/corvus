from __future__ import annotations

import json
from typing import Any, Dict

from collections import defaultdict

from datetime import datetime, time, timedelta

from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.db.models import Q, Count
from django.db.models.functions import TruncDate
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import User
from core.models import (
    Card,
    Deck,
    Import,
    ImportSession,
    KnowledgeMap,
    KnowledgeNode,
    Review,
    SchedulingState,
    StudySet,
)
from core.scheduling import ensure_state
from core.services.review import get_next_card, get_today_summary, grade_card_for_user
from core.services.decks import get_deck_by_full_path, split_deck_path
from core.services.knowledge_maps import KnowledgeMapImportError, import_knowledge_map_from_payload
from import_anki.services import AnkiImportError, process_apkg_archive
from import_md.services import (
    MarkdownImportError,
    apply_markdown_session,
    cancel_markdown_session,
    prepare_markdown_note_session,
    process_markdown_archive,
)


def _json_error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({'error': message}, status=status)


def _parse_json(request: HttpRequest) -> Dict[str, Any]:
    try:
        if not request.body:
            return {}
        return json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError as exc:  # pragma: no cover - error path exercised in tests
        raise ValueError(f'Invalid JSON payload: {exc}')


def _require_user(request: HttpRequest) -> User:
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        raise PermissionError('authentication required')
    return user  # type: ignore[return-value]


def _parse_deck_id(raw_deck_id: Any) -> int:
    try:
        return int(raw_deck_id)
    except (TypeError, ValueError) as exc:
        raise ValueError('deck_id must be an integer') from exc


def _get_deck_for_user(raw_deck_id: Any, user: User) -> Deck:
    deck_id = _parse_deck_id(raw_deck_id)
    try:
        return Deck.objects.get(id=deck_id, user=user)
    except Deck.DoesNotExist as exc:
        raise LookupError('deck not found') from exc


def _deck_to_dict(deck: Deck) -> dict:
    return {
        'id': deck.id,
        'name': deck.name,
        'description': deck.description,
        'created_at': deck.created_at.isoformat(),
    }


def _card_to_dict(card: Card) -> dict:
    state = getattr(card, 'scheduling_state', None) or ensure_state(card)
    return {
        'id': str(card.id),
        'import_id': card.import_id,
        'deck_id': card.deck_id,
        'front_md': card.front_md,
        'back_md': card.back_md,
        'tags': card.tags,
        'media': card.media,
        'source_path': card.source_path,
        'source_anchor': card.source_anchor,
        'created_at': card.created_at.isoformat(),
        'updated_at': card.updated_at.isoformat(),
        'scheduling': {
            'queue_status': state.queue_status,
            'due_at': state.due_at.isoformat() if state.due_at else None,
            'ease': state.ease,
            'interval_days': state.interval_days,
            'reps': state.reps,
            'lapses': state.lapses,
            'last_rating': state.last_rating,
        },
    }


def _parse_attachment_manifest(raw_manifest: str | None) -> list[dict[str, str]]:
    if not raw_manifest:
        return []
    try:
        payload = json.loads(raw_manifest)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid attachment_manifest: {exc}') from exc
    if not isinstance(payload, list):
        raise ValueError('attachment_manifest must be a JSON list')
    manifest: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError('attachment_manifest items must be objects')
        field = item.get('field')
        path = item.get('path')
        if not isinstance(field, str) or not field.strip():
            raise ValueError('attachment_manifest items require a non-empty field')
        if not isinstance(path, str) or not path.strip():
            raise ValueError('attachment_manifest items require a non-empty path')
        manifest.append({'field': field.strip(), 'path': path.strip()})
    return manifest


def _extract_obsidian_attachments(request: HttpRequest) -> dict[str, bytes]:
    manifest = _parse_attachment_manifest(request.POST.get('attachment_manifest'))
    attachments: dict[str, bytes] = {}
    for item in manifest:
        upload = request.FILES.get(item['field'])
        if upload is None:
            raise ValueError(f"attachment file missing for field '{item['field']}'")
        attachments[item['path']] = upload.read()
    return attachments


def _target_deck_path(root_deck_path: str, card_payload: dict[str, Any]) -> str:
    deck_parts = [str(part).strip() for part in (card_payload.get('deck_path') or []) if str(part).strip()]
    if not root_deck_path:
        return '/'.join(deck_parts)
    if not deck_parts:
        return root_deck_path
    return '/'.join([root_deck_path, *deck_parts])


def _normalise_obsidian_note_path(raw_path: Any) -> str:
    if not isinstance(raw_path, str):
        raise ValueError('obsidian_path must be a string')
    path = raw_path.strip().replace('\\', '/')
    if not path:
        raise ValueError('obsidian_path is required')
    if '#' in path or '^' in path:
        raise ValueError('linked note paths must not include headings or blocks')
    parts = [part.strip() for part in path.split('/') if part.strip()]
    if not parts:
        raise ValueError('obsidian_path is required')
    if any(part in {'.', '..'} for part in parts):
        raise ValueError('obsidian_path cannot contain relative path segments')
    normalised = '/'.join(parts)
    if not normalised.lower().endswith('.md'):
        raise ValueError('linked note paths must end in .md')
    return normalised


def _obsidian_note_path_to_deck_path(root_deck_path: str, obsidian_path: str) -> str:
    note_stem = obsidian_path[:-3]
    note_parts = [part for part in note_stem.split('/') if part]
    return '/'.join([*split_deck_path(root_deck_path), *note_parts])


def _parse_obsidian_study_set_links(raw_links: Any) -> list[dict[str, str]]:
    if not isinstance(raw_links, list):
        raise ValueError('links must be a list')
    if not raw_links:
        raise ValueError('at least one linked markdown note is required')

    links: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for item in raw_links:
        if not isinstance(item, dict):
            raise ValueError('link items must be objects')
        obsidian_path = _normalise_obsidian_note_path(item.get('obsidian_path') or item.get('obsidianPath'))
        if obsidian_path in seen_paths:
            continue
        seen_paths.add(obsidian_path)
        link_text = str(item.get('link_text') or item.get('linkText') or obsidian_path).strip()
        links.append(
            {
                'link_text': link_text or obsidian_path,
                'obsidian_path': obsidian_path,
            }
        )
    return links


def _study_set_deck_to_dict(deck: Deck, *, obsidian_path: str, link_text: str, target_deck_path: str) -> dict:
    return {
        'id': deck.id,
        'name': deck.name,
        'full_path': deck.full_path(),
        'obsidian_path': obsidian_path,
        'link_text': link_text,
        'target_deck_path': target_deck_path,
    }


def _study_set_to_dict(study_set: StudySet, decks: list[Deck] | None = None) -> dict:
    selected_decks = decks if decks is not None else list(study_set.decks.all())
    return {
        'id': study_set.id,
        'name': study_set.name,
        'kind': study_set.kind,
        'deck_ids': [deck.id for deck in selected_decks],
        'decks': [{'id': deck.id, 'name': deck.name, 'full_path': deck.full_path()} for deck in selected_decks],
    }


def _build_obsidian_study_set_preview(user: User, payload: dict[str, Any]) -> tuple[dict, list[Deck], StudySet | None]:
    name = str(payload.get('name') or '').strip()
    root_deck_path = str(payload.get('root_deck_path') or '').strip()
    source_path = str(payload.get('source_path') or '').strip()
    source_hash = str(payload.get('source_hash') or '').strip()

    if not name:
        raise ValueError('name required')
    if not root_deck_path:
        raise ValueError('root_deck_path required')

    root_deck = get_deck_by_full_path(user, root_deck_path)
    if root_deck is None:
        raise LookupError('root deck not found')

    links = _parse_obsidian_study_set_links(payload.get('links'))
    existing = StudySet.objects.filter(user=user, name=name, kind=StudySet.KIND_CUSTOM).order_by('id').first()
    if existing is None:
        existing = StudySet.objects.filter(user=user, name=name).order_by('id').first()

    decks: list[Deck] = []
    deck_payloads: list[dict] = []
    missing: list[dict] = []
    seen_deck_ids: set[int] = set()
    resolved_root_path = root_deck.full_path()
    for link in links:
        target_deck_path = _obsidian_note_path_to_deck_path(resolved_root_path, link['obsidian_path'])
        deck = get_deck_by_full_path(user, target_deck_path)
        if deck is None:
            missing.append(
                {
                    'link_text': link['link_text'],
                    'obsidian_path': link['obsidian_path'],
                    'target_deck_path': target_deck_path,
                }
            )
            continue
        if deck.id in seen_deck_ids:
            continue
        seen_deck_ids.add(deck.id)
        decks.append(deck)
        deck_payloads.append(
            _study_set_deck_to_dict(
                deck,
                obsidian_path=link['obsidian_path'],
                link_text=link['link_text'],
                target_deck_path=target_deck_path,
            )
        )

    errors = []
    if not decks:
        errors.append('No referenced decks were found.')
    if missing:
        errors.append('Some referenced decks do not exist in Corvus.')

    preview = {
        'name': name,
        'source_path': source_path,
        'source_hash': source_hash,
        'root_deck_path': resolved_root_path,
        'action': 'update' if existing else 'create',
        'will_update': existing is not None,
        'existing_study_set_id': existing.id if existing else None,
        'has_errors': bool(errors),
        'errors': errors,
        'summary': {
            'deck_count': len(deck_payloads),
            'missing_count': len(missing),
        },
        'decks': deck_payloads,
        'missing': missing,
    }
    return preview, decks, existing


def _serialise_obsidian_session(session: ImportSession) -> dict[str, Any]:
    payload = dict(session.payload or {})
    obsidian = payload.get('obsidian') or {}
    root_deck_path = str(obsidian.get('root_deck_path') or '')
    cards_payload = []
    for card in payload.get('cards', []):
        existing = card.get('existing') or {}
        cards_payload.append(
            {
                'index': card.get('index'),
                'marker_line': card.get('marker_line'),
                'marker_kind': card.get('marker_kind'),
                'import_id': card.get('import_id'),
                'front_md': card.get('front_md'),
                'back_md': card.get('back_md'),
                'existing': bool(existing),
                'existing_card_id': existing.get('card_id') if isinstance(existing, dict) else None,
                'has_changes': bool(card.get('has_changes')),
                'unchanged': bool(card.get('unchanged')),
                'warnings': list(card.get('warnings') or []),
                'errors': list(card.get('errors') or []),
                'target_deck_path': _target_deck_path(root_deck_path, card),
                'deck_path': list(card.get('deck_path') or []),
                'will_write_back_id': bool(card.get('import_id')) and not bool(card.get('had_explicit_import_id')),
            }
        )
    return {
        'session_id': str(session.id),
        'status': session.status,
        'source_name': session.source_name,
        'source_hash': obsidian.get('source_hash', ''),
        'source_path': obsidian.get('source_path', ''),
        'root_deck_path': root_deck_path,
        'summary': payload.get('summary', {}),
        'planned_decks': list(obsidian.get('planned_decks') or []),
        'has_errors': bool(payload.get('summary', {}).get('has_errors')),
        'cards': cards_payload,
    }


def _knowledge_map_to_dict(knowledge_map: KnowledgeMap, include_nodes: bool = False) -> dict:
    payload = {
        'slug': knowledge_map.slug,
        'name': knowledge_map.name,
        'description': knowledge_map.description,
        'metadata': knowledge_map.metadata,
        'tag_prefix': f'km:{knowledge_map.slug}:',
        'node_count': knowledge_map.nodes.count(),
        'created_at': knowledge_map.created_at.isoformat(),
        'updated_at': knowledge_map.updated_at.isoformat(),
    }
    if include_nodes:
        payload['nodes'] = _knowledge_nodes_as_tree(knowledge_map)
    return payload


def _knowledge_nodes_as_tree(knowledge_map: KnowledgeMap) -> list[dict]:
    nodes = list(
        knowledge_map.nodes.select_related('parent').order_by('parent_id', 'display_order', 'id')
    )
    children: dict[int | None, list[KnowledgeNode]] = defaultdict(list)
    for node in nodes:
        children[node.parent_id].append(node)
    return [_knowledge_node_to_dict(node, children) for node in children.get(None, [])]


def _knowledge_node_to_dict(
    node: KnowledgeNode, children: dict[int | None, list[KnowledgeNode]]
) -> dict:
    payload = {
        'key': node.identifier,
        'title': node.title,
        'definition': node.definition,
        'guidance': node.guidance,
        'sources': node.sources,
        'metadata': node.metadata,
        'tag': node.tag_value,
    }
    child_nodes = children.get(node.id, [])
    if child_nodes:
        payload['children'] = [
            _knowledge_node_to_dict(child, children)
            for child in sorted(child_nodes, key=lambda c: (c.display_order, c.id))
        ]
    else:
        payload['children'] = []
    return payload


@csrf_exempt
@require_http_methods(['POST'])
def auth_register(request: HttpRequest) -> JsonResponse:
    try:
        payload = _parse_json(request)
    except ValueError as exc:
        return _json_error(str(exc))
    email = payload.get('email')
    password = payload.get('password')
    if not email or not password:
        return _json_error('email and password required')
    if User.objects.filter(email=email).exists():
        return _json_error('email already registered')
    user = User.objects.create_user(email=email, password=password)
    login(request, user)
    return JsonResponse({'id': user.id, 'email': user.email}, status=201)


@csrf_exempt
@require_http_methods(['POST'])
def auth_login(request: HttpRequest) -> JsonResponse:
    try:
        payload = _parse_json(request)
    except ValueError as exc:
        return _json_error(str(exc))
    email = payload.get('email')
    password = payload.get('password')
    if not email or not password:
        return _json_error('email and password required')
    user = authenticate(request, email=email, password=password)
    if user is None:
        return _json_error('invalid credentials', status=401)
    login(request, user)
    return JsonResponse({'id': user.id, 'email': user.email})


@csrf_exempt
@require_http_methods(['POST'])
def auth_logout(request: HttpRequest) -> JsonResponse:
    logout(request)
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def decks_collection(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)

    if request.method == 'GET':
        decks = Deck.objects.for_user(user).order_by('name')
        return JsonResponse([_deck_to_dict(deck) for deck in decks], safe=False)

    try:
        payload = _parse_json(request)
    except ValueError as exc:
        return _json_error(str(exc))
    name = payload.get('name')
    if not name:
        return _json_error('name required')
    description = payload.get('description', '')
    deck = Deck.objects.create(user=user, name=name, description=description)
    return JsonResponse(_deck_to_dict(deck), status=201)


@csrf_exempt
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def deck_detail(request: HttpRequest, deck_id: int) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        deck = Deck.objects.get(id=deck_id, user=user)
    except Deck.DoesNotExist:
        return _json_error('deck not found', status=404)

    if request.method == 'GET':
        return JsonResponse(_deck_to_dict(deck))

    if request.method == 'PATCH':
        try:
            payload = _parse_json(request)
        except ValueError as exc:
            return _json_error(str(exc))
        if 'name' in payload:
            deck.name = payload['name']
        if 'description' in payload:
            deck.description = payload['description']
        deck.save(update_fields=['name', 'description'])
        return JsonResponse(_deck_to_dict(deck))

    deck.delete()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def cards_collection(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)

    if request.method == 'GET':
        deck_id_raw = request.GET.get('deck_id')
        search = request.GET.get('q')
        tag = request.GET.get('tag')
        cards = Card.objects.for_user(user).select_related('deck', 'scheduling_state')
        if deck_id_raw:
            try:
                deck_id = _parse_deck_id(deck_id_raw)
            except ValueError as exc:
                return _json_error(str(exc))
            cards = cards.filter(deck_id=deck_id)
        if tag:
            cards = cards.filter(tags__contains=[tag.strip()])
        if search:
            cards = cards.filter(Q(front_md__icontains=search) | Q(back_md__icontains=search))
        cards = cards.order_by('-updated_at')
        return JsonResponse([_card_to_dict(card) for card in cards], safe=False)

    try:
        payload = _parse_json(request)
    except ValueError as exc:
        return _json_error(str(exc))
    deck_id_raw = payload.get('deck_id')
    if not deck_id_raw:
        return _json_error('deck_id required')
    try:
        deck = _get_deck_for_user(deck_id_raw, user)
    except ValueError as exc:
        return _json_error(str(exc))
    except LookupError:
        return _json_error('deck not found', status=404)
    tags = payload.get('tags', [])
    if not isinstance(tags, list):
        return _json_error('tags must be a list')
    card = Card.objects.create(
        user=user,
        deck=deck,
        front_md=payload.get('front_md', ''),
        back_md=payload.get('back_md', ''),
        tags=[str(tag) for tag in tags],
    )
    ensure_state(card)
    return JsonResponse(_card_to_dict(card), status=201)


@csrf_exempt
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def card_detail(request: HttpRequest, card_id: str) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        card = Card.objects.select_related('deck', 'scheduling_state').get(id=card_id, user=user)
    except Card.DoesNotExist:
        return _json_error('card not found', status=404)

    if request.method == 'GET':
        return JsonResponse(_card_to_dict(card))

    if request.method == 'PATCH':
        try:
            payload = _parse_json(request)
        except ValueError as exc:
            return _json_error(str(exc))
        if 'deck_id' in payload:
            try:
                deck = Deck.objects.get(id=payload['deck_id'], user=user)
            except Deck.DoesNotExist:
                return _json_error('deck not found', status=404)
            card.deck = deck
        if 'front_md' in payload:
            card.front_md = payload['front_md']
        if 'back_md' in payload:
            card.back_md = payload['back_md']
        if 'tags' in payload:
            tags = payload['tags']
            if not isinstance(tags, list):
                return _json_error('tags must be a list')
            card.tags = [str(tag) for tag in tags]
        card.save()
        return JsonResponse(_card_to_dict(card))

    card.delete()
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(['GET'])
def knowledge_maps_collection(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    maps = (
        KnowledgeMap.objects.for_user(user)
        .annotate(total_nodes=Count('nodes'))
        .order_by('name')
    )
    payload = [
        {
            'slug': km.slug,
            'name': km.name,
            'description': km.description,
            'metadata': km.metadata,
            'tag_prefix': f'km:{km.slug}:',
            'node_count': km.total_nodes,
            'created_at': km.created_at.isoformat(),
            'updated_at': km.updated_at.isoformat(),
        }
        for km in maps
    ]
    return JsonResponse(payload, safe=False)


@csrf_exempt
@require_http_methods(['GET'])
def knowledge_map_detail(request: HttpRequest, map_slug: str) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        knowledge_map = KnowledgeMap.objects.get(user=user, slug=map_slug)
    except KnowledgeMap.DoesNotExist:
        return _json_error('knowledge map not found', status=404)
    return JsonResponse(_knowledge_map_to_dict(knowledge_map, include_nodes=True))


@csrf_exempt
@require_http_methods(['POST'])
def knowledge_map_import(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        payload = _parse_json(request)
    except ValueError as exc:
        return _json_error(str(exc))
    try:
        result = import_knowledge_map_from_payload(user=user, payload=payload)
    except KnowledgeMapImportError as exc:
        return _json_error(str(exc))
    response = {
        'slug': result.knowledge_map.slug,
        'name': result.knowledge_map.name,
        'node_count': result.knowledge_map.nodes.count(),
        'created_nodes': result.created_nodes,
        'replaced_nodes': result.replaced_nodes,
        'created_map': result.created_map,
    }
    status_code = 201 if result.created_map else 200
    return JsonResponse(response, status=status_code)


@csrf_exempt
@require_http_methods(['GET'])
def review_today(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    deck = None
    deck_id_raw = request.GET.get('deck_id')
    if deck_id_raw:
        try:
            deck = _get_deck_for_user(deck_id_raw, user)
        except ValueError as exc:
            return _json_error(str(exc), status=400)
        except LookupError:
            return _json_error('deck not found', status=404)
    summary = get_today_summary(user, deck)
    return JsonResponse({
        'new_count': summary.new_count,
        'review_count': summary.review_count,
        'due_count': summary.due_count,
    })


@csrf_exempt
@require_http_methods(['POST'])
def review_next(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        payload = _parse_json(request)
    except ValueError as exc:
        return _json_error(str(exc))
    deck = None
    deck_id_raw = payload.get('deck_id')
    if deck_id_raw:
        try:
            deck = _get_deck_for_user(deck_id_raw, user)
        except ValueError as exc:
            return _json_error(str(exc), status=400)
        except LookupError:
            return _json_error('deck not found', status=404)
    card = get_next_card(user, deck)
    if not card:
        return JsonResponse({'card_id': None})
    ensure_state(card)
    return JsonResponse({'card_id': str(card.id), 'front_md': card.front_md})


@csrf_exempt
@require_http_methods(['POST'])
def review_reveal(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        payload = _parse_json(request)
    except ValueError as exc:
        return _json_error(str(exc))
    card_id = payload.get('card_id')
    if not card_id:
        return _json_error('card_id required')
    try:
        card = Card.objects.get(id=card_id, user=user)
    except Card.DoesNotExist:
        return _json_error('card not found', status=404)
    ensure_state(card)
    return JsonResponse({'card_id': str(card.id), 'back_md': card.back_md})


@csrf_exempt
@require_http_methods(['POST'])
def review_grade(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        payload = _parse_json(request)
    except ValueError as exc:
        return _json_error(str(exc))
    card_id = payload.get('card_id')
    rating = payload.get('rating')
    deck_id = payload.get('deck_id')
    if card_id is None or rating is None:
        return _json_error('card_id and rating required')
    try:
        rating_int = int(rating)
    except (TypeError, ValueError):
        return _json_error('rating must be an integer between 0 and 3')
    if rating_int not in {0, 1, 2, 3}:
        return _json_error('rating must be between 0 and 3')
    deck = None
    if deck_id:
        try:
            deck = _get_deck_for_user(deck_id, user)
        except ValueError as exc:
            return _json_error(str(exc), status=400)
        except LookupError:
            return _json_error('deck not found', status=404)
    if not Card.objects.filter(id=card_id, user=user).exists():
        return _json_error('card not found', status=404)
    grade_card_for_user(user=user, card_id=card_id, rating=rating_int)
    next_card = get_next_card(user, deck)
    return JsonResponse({'next_available': next_card is not None})


@require_http_methods(['GET'])
def analytics_heatmap_summary(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)

    today = timezone.localdate()
    year_param = request.GET.get('year')
    if year_param:
        try:
            selected_year = int(year_param)
        except ValueError:
            return _json_error('year must be an integer', status=400)
        start_date = datetime(selected_year, 1, 1).date()
        end_date = datetime(selected_year, 12, 31).date()
    else:
        try:
            past_days = int(request.GET.get('past_days', 365))
            future_days = int(request.GET.get('future_days', 90))
        except ValueError:
            return _json_error('past_days and future_days must be integers', status=400)
        start_date = today - timedelta(days=max(past_days, 0))
        end_date = today + timedelta(days=max(future_days, 0))

    start_dt = timezone.make_aware(datetime.combine(start_date, time.min))
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max))

    review_rows = (
        Review.objects.filter(user=user, reviewed_at__gte=start_dt, reviewed_at__lte=end_dt)
        .annotate(day=TruncDate('reviewed_at'))
        .values('day')
        .annotate(count=Count('id'))
    )
    due_rows = (
          SchedulingState.objects.filter(
              card__user=user,
              due_at__isnull=False,
              due_at__gte=start_dt,
              due_at__lte=end_dt,
          )
          .annotate(day=TruncDate('due_at'))
          .values('day')
          .annotate(count=Count('card_id'))
    )

    day_lookup: dict[str, dict[str, int]] = {}
    for row in review_rows:
        key = row['day'].isoformat() if row['day'] else None
        if not key:
            continue
        day_lookup.setdefault(key, {'reviewed': 0, 'due': 0})['reviewed'] = row['count']
    for row in due_rows:
        key = row['day'].isoformat() if row['day'] else None
        if not key:
            continue
        day_lookup.setdefault(key, {'reviewed': 0, 'due': 0})['due'] = row['count']

    days: list[dict[str, object]] = []
    current = start_date
    current_streak = 0
    longest_streak = 0
    running_streak = 0
    while current <= end_date:
        key = current.isoformat()
        info = day_lookup.get(key, {'reviewed': 0, 'due': 0})
        reviewed = int(info.get('reviewed', 0))
        due = int(info.get('due', 0))
        days.append({'date': key, 'reviewed': reviewed, 'due': due})
        if current <= today:
            if reviewed > 0:
                running_streak += 1
                if current == today:
                    current_streak = running_streak
            else:
                running_streak = 0
            if running_streak > longest_streak:
                longest_streak = running_streak
        current += timedelta(days=1)

    payload = {
        'start': start_date.isoformat(),
        'end': end_date.isoformat(),
        'today': today.isoformat(),
        'current_streak': current_streak,
        'longest_streak': longest_streak,
        'days': days,
    }
    return JsonResponse(payload)


@require_http_methods(['GET'])
def analytics_heatmap_day(request: HttpRequest, date_str: str) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return _json_error('invalid date format, expected YYYY-MM-DD', status=400)

    reviews = (
        Review.objects.filter(user=user, reviewed_at__date=target_date)
        .select_related('card__deck')
        .order_by('reviewed_at')
    )
    review_payload = [
        {
            'card_id': str(review.card_id),
            'deck': review.card.deck.full_path(),
            'rating': review.rating,
            'reviewed_at': timezone.localtime(review.reviewed_at).isoformat(),
            'front_md': review.card.front_md,
        }
        for review in reviews
    ]

    due_states = (
        SchedulingState.objects.filter(card__user=user, due_at__date=target_date)
        .select_related('card__deck')
        .order_by('due_at')
    )
    due_payload = [
        {
            'card_id': str(state.card_id),
            'deck': state.card.deck.full_path(),
            'queue_status': state.queue_status,
            'due_at': timezone.localtime(state.due_at).isoformat() if state.due_at else None,
        }
        for state in due_states
    ]

    return JsonResponse({
        'date': target_date.isoformat(),
        'reviews': review_payload,
        'due': due_payload,
    })


def _get_obsidian_session(user: User, session_id: str) -> ImportSession:
    try:
        session = ImportSession.objects.get(id=session_id, user=user, kind='markdown')
    except ImportSession.DoesNotExist as exc:
        raise LookupError('preview session not found') from exc
    obsidian = (session.payload or {}).get('obsidian') or {}
    if obsidian.get('source_system') != 'obsidian':
        raise LookupError('preview session not found')
    return session


@csrf_exempt
@require_http_methods(['POST'])
def obsidian_preview_create(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)

    source_path = (request.POST.get('source_path') or '').strip()
    content = request.POST.get('content')
    root_deck_path = (request.POST.get('root_deck_path') or '').strip()
    source_hash = (request.POST.get('source_hash') or '').strip()

    if not source_path or content is None or not root_deck_path or not source_hash:
        return _json_error('source_path, content, root_deck_path, and source_hash required')

    root_deck = get_deck_by_full_path(user, root_deck_path)
    if root_deck is None:
        return _json_error('root deck not found', status=404)

    try:
        attachments = _extract_obsidian_attachments(request)
        session = prepare_markdown_note_session(
            user=user,
            root_deck=root_deck,
            source_path=source_path,
            content=content,
            attachments=attachments,
            source_hash=source_hash,
        )
    except (ValueError, MarkdownImportError) as exc:
        return _json_error(str(exc), status=400)

    return JsonResponse(_serialise_obsidian_session(session), status=201)


@require_http_methods(['GET'])
def obsidian_preview_detail(request: HttpRequest, session_id: str) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        session = _get_obsidian_session(user, session_id)
    except LookupError as exc:
        return _json_error(str(exc), status=404)
    return JsonResponse(_serialise_obsidian_session(session))


@csrf_exempt
@require_http_methods(['POST'])
def obsidian_preview_apply(request: HttpRequest, session_id: str) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        session = _get_obsidian_session(user, session_id)
    except LookupError as exc:
        return _json_error(str(exc), status=404)
    if session.status != 'ready':
        return _json_error('preview session is not ready to apply', status=409)
    try:
        payload = _parse_json(request)
    except ValueError as exc:
        return _json_error(str(exc))

    source_hash = str(payload.get('source_hash') or '').strip()
    expected_hash = str(((session.payload or {}).get('obsidian') or {}).get('source_hash') or '').strip()
    if not source_hash:
        return _json_error('source_hash required')
    if source_hash != expected_hash:
        return _json_error('source_hash does not match preview session', status=409)

    raw_decisions = payload.get('decisions', [])
    if raw_decisions is None:
        raw_decisions = []
    if not isinstance(raw_decisions, list):
        return _json_error('decisions must be a list')

    decisions: dict[int, str] = {}
    for item in raw_decisions:
        if not isinstance(item, dict):
            return _json_error('decisions items must be objects')
        try:
            index = int(item.get('index'))
        except (TypeError, ValueError):
            return _json_error('decision index must be an integer')
        action = str(item.get('action') or '').strip().lower()
        if action not in {'apply', 'skip'}:
            return _json_error("decision action must be 'apply' or 'skip'")
        decisions[index] = 'imported' if action == 'apply' else 'skip'

    try:
        import_record = apply_markdown_session(session, decisions=decisions)
    except MarkdownImportError as exc:
        return _json_error(str(exc), status=400)

    session.refresh_from_db()
    applied_cards = list((session.payload or {}).get('apply_results') or [])
    return JsonResponse(
        {
            'session_id': str(session.id),
            'status': session.status,
            'summary': {
                **import_record.summary,
                'failed': 0,
            },
            'cards': applied_cards,
        }
    )


@csrf_exempt
@require_http_methods(['POST'])
def obsidian_preview_cancel(request: HttpRequest, session_id: str) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        session = _get_obsidian_session(user, session_id)
    except LookupError as exc:
        return _json_error(str(exc), status=404)
    cancel_markdown_session(session)
    return JsonResponse({'session_id': str(session.id), 'status': session.status})


@csrf_exempt
@require_http_methods(['POST'])
def obsidian_study_set_preview(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        payload = _parse_json(request)
        if not isinstance(payload, dict):
            return _json_error('payload must be an object')
        preview, _, _ = _build_obsidian_study_set_preview(user, payload)
    except ValueError as exc:
        return _json_error(str(exc))
    except LookupError as exc:
        return _json_error(str(exc), status=404)
    return JsonResponse(preview)


@csrf_exempt
@require_http_methods(['POST'])
def obsidian_study_set_apply(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        payload = _parse_json(request)
        if not isinstance(payload, dict):
            return _json_error('payload must be an object')
        preview, decks, study_set = _build_obsidian_study_set_preview(user, payload)
    except ValueError as exc:
        return _json_error(str(exc))
    except LookupError as exc:
        return _json_error(str(exc), status=404)

    if preview['has_errors']:
        return JsonResponse(
            {
                **preview,
                'error': 'all referenced decks must exist before applying',
            },
            status=400,
        )

    with transaction.atomic():
        action = 'updated'
        if study_set is None:
            study_set = StudySet.objects.create(
                user=user,
                name=preview['name'],
                kind=StudySet.KIND_CUSTOM,
                deck=None,
                tag='',
                tags=[],
                filenames=[],
            )
            action = 'created'
        else:
            study_set.name = preview['name']
            study_set.kind = StudySet.KIND_CUSTOM
            study_set.deck = None
            study_set.tag = ''
            study_set.tags = []
            study_set.filenames = []
            study_set.save(update_fields=['name', 'kind', 'deck', 'tag', 'tags', 'filenames', 'updated_at'])

        study_set.decks.set(decks)
    return JsonResponse(
        {
            'status': 'applied',
            'action': action,
            'study_set': _study_set_to_dict(study_set, decks),
        },
        status=201 if action == 'created' else 200,
    )


@csrf_exempt
@require_http_methods(['POST'])
def import_markdown(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    deck_id_raw = request.POST.get('deck_id')
    archive = request.FILES.get('archive')
    if not deck_id_raw or archive is None:
        return _json_error('deck_id and archive required')
    try:
        deck = _get_deck_for_user(deck_id_raw, user)
    except ValueError as exc:
        return _json_error(str(exc))
    except LookupError:
        return _json_error('deck not found', status=404)
    try:
        import_record = process_markdown_archive(user=user, deck=deck, uploaded_file=archive)
    except MarkdownImportError as exc:
        return _json_error(str(exc), status=400)
    return JsonResponse({'import_id': import_record.id, 'summary': import_record.summary})


@csrf_exempt
@require_http_methods(['POST'])
def import_anki(request: HttpRequest) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    deck_id_raw = request.POST.get('deck_id')
    package = request.FILES.get('package')
    if not deck_id_raw or package is None:
        return _json_error('deck_id and package required')
    try:
        deck = _get_deck_for_user(deck_id_raw, user)
    except ValueError as exc:
        return _json_error(str(exc))
    except LookupError:
        return _json_error('deck not found', status=404)
    try:
        import_record = process_apkg_archive(user=user, deck=deck, uploaded_file=package)
    except AnkiImportError as exc:
        return _json_error(str(exc), status=400)
    return JsonResponse({'import_id': import_record.id, 'summary': import_record.summary})


@csrf_exempt
@require_http_methods(['GET'])
def import_status(request: HttpRequest, import_id: int) -> JsonResponse:
    try:
        user = _require_user(request)
    except PermissionError as exc:
        return _json_error(str(exc), status=401)
    try:
        import_record = Import.objects.get(id=import_id, user=user)
    except Import.DoesNotExist:
        return _json_error('import not found', status=404)
    return JsonResponse(
        {
            'id': import_record.id,
            'kind': import_record.kind,
            'status': import_record.status,
            'summary': import_record.summary,
        }
    )
