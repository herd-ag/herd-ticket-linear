"""Tests for LinearTicketAdapter."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from unittest.mock import Mock, patch

import pytest

from herd_core.adapters.tickets import TicketAdapter
from herd_core.types import TicketPriority, TicketRecord, TransitionResult
from herd_ticket_linear import LinearTicketAdapter


@pytest.fixture
def adapter():
    """Create adapter instance with state mapping."""
    return LinearTicketAdapter(
        api_key="test-api-key",
        team_id="test-team-id",
        state_mapping={
            "backlog": "f98ff170-87bd-4a1c-badc-4b67cd37edec",
            "in_progress": "77631f63-b27b-45a5-8b04-f9f82b4facde",
            "done": "42bad6cf-cfb7-4dd2-9dc4-c0c3014bfc5f",
        },
    )


def test_isinstance_protocol_check(adapter):
    """Test that adapter satisfies TicketAdapter protocol."""
    assert isinstance(adapter, TicketAdapter)


def test_resolve_state_id(adapter):
    """Test state ID resolution."""
    # Test mapping lookup
    assert adapter._resolve_state_id("in_progress") == "77631f63-b27b-45a5-8b04-f9f82b4facde"

    # Test UUID passthrough
    uuid = "12345678-1234-1234-1234-123456789abc"
    assert adapter._resolve_state_id(uuid) == uuid

    # Test unknown status
    with pytest.raises(ValueError, match="not found in state_mapping"):
        adapter._resolve_state_id("unknown_status")


@patch("urllib.request.urlopen")
def test_get_ticket(mock_urlopen, adapter):
    """Test fetching a ticket."""
    mock_response = Mock()
    mock_response.read.return_value = json.dumps({
        "data": {
            "searchIssues": {
                "nodes": [
                    {
                        "id": "issue-uuid",
                        "identifier": "DBC-123",
                        "title": "Test ticket",
                        "description": "Test description",
                        "createdAt": "2026-02-14T10:00:00.000Z",
                        "updatedAt": "2026-02-14T11:00:00.000Z",
                        "state": {"id": "state-uuid", "name": "In Progress"},
                        "priority": 2,
                        "team": {"id": "team-uuid", "name": "Engineering"},
                        "project": {"id": "project-uuid", "name": "Q1 Goals"},
                        "assignee": {"id": "user-uuid", "name": "John Doe"},
                        "labels": {
                            "nodes": [
                                {"id": "label-1", "name": "bug"},
                                {"id": "label-2", "name": "backend"},
                            ]
                        },
                    }
                ]
            }
        }
    }).encode()
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)
    mock_urlopen.return_value = mock_response

    ticket = adapter.get("DBC-123")

    assert isinstance(ticket, TicketRecord)
    assert ticket.id == "DBC-123"
    assert ticket.title == "Test ticket"
    assert ticket.description == "Test description"
    assert ticket.status == "In Progress"
    assert ticket.priority == TicketPriority.HIGH
    assert ticket.project == "Q1 Goals"
    assert ticket.assignee == "John Doe"
    assert ticket.labels == ["bug", "backend"]
    assert isinstance(ticket.created_at, datetime)
    assert isinstance(ticket.modified_at, datetime)


@patch("urllib.request.urlopen")
def test_get_ticket_not_found(mock_urlopen, adapter):
    """Test fetching non-existent ticket."""
    mock_response = Mock()
    mock_response.read.return_value = json.dumps({
        "data": {"searchIssues": {"nodes": []}}
    }).encode()
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)
    mock_urlopen.return_value = mock_response

    with pytest.raises(Exception, match="not found"):
        adapter.get("DBC-999")


@patch("urllib.request.urlopen")
def test_create_ticket(mock_urlopen, adapter):
    """Test creating a ticket."""
    mock_response = Mock()
    mock_response.read.return_value = json.dumps({
        "data": {
            "issueCreate": {
                "success": True,
                "issue": {
                    "id": "new-issue-uuid",
                    "identifier": "DBC-124",
                    "title": "New ticket",
                    "state": {"id": "state-uuid", "name": "Backlog"},
                },
            }
        }
    }).encode()
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)
    mock_urlopen.return_value = mock_response

    ticket_id = adapter.create(
        title="New ticket",
        description="Test description",
        priority=2,
        labels=["label-1", "label-2"],
    )

    assert ticket_id == "DBC-124"

    # Verify GraphQL mutation was called with correct input
    call_args = mock_urlopen.call_args
    request = call_args[0][0]
    body = json.loads(request.data.decode())

    assert "mutation CreateIssue" in body["query"]
    assert body["variables"]["input"]["title"] == "New ticket"
    assert body["variables"]["input"]["description"] == "Test description"
    assert body["variables"]["input"]["priority"] == 2
    assert body["variables"]["input"]["labelIds"] == ["label-1", "label-2"]


@patch("urllib.request.urlopen")
def test_create_ticket_failure(mock_urlopen, adapter):
    """Test create ticket failure."""
    mock_response = Mock()
    mock_response.read.return_value = json.dumps({
        "data": {"issueCreate": {"success": False}}
    }).encode()
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)
    mock_urlopen.return_value = mock_response

    with pytest.raises(Exception, match="Failed to create"):
        adapter.create(title="Test")


@patch("urllib.request.urlopen")
def test_update_ticket(mock_urlopen, adapter):
    """Test updating a ticket."""
    # Mock both the search query and the update mutation
    search_response = Mock()
    search_response.read.return_value = json.dumps({
        "data": {
            "searchIssues": {
                "nodes": [{"id": "issue-uuid", "identifier": "DBC-123"}]
            }
        }
    }).encode()
    search_response.__enter__ = Mock(return_value=search_response)
    search_response.__exit__ = Mock(return_value=False)

    update_response = Mock()
    update_response.read.return_value = json.dumps({
        "data": {
            "issueUpdate": {
                "success": True,
                "issue": {"id": "issue-uuid", "identifier": "DBC-123"},
            }
        }
    }).encode()
    update_response.__enter__ = Mock(return_value=update_response)
    update_response.__exit__ = Mock(return_value=False)

    # Return different responses for each call
    mock_urlopen.side_effect = [search_response, update_response]

    adapter.update("DBC-123", title="Updated title", priority=1)

    # Verify update mutation was called
    assert mock_urlopen.call_count == 2
    update_call = mock_urlopen.call_args_list[1]
    body = json.loads(update_call[0][0].data.decode())

    assert "mutation UpdateIssue" in body["query"]
    assert body["variables"]["input"]["title"] == "Updated title"
    assert body["variables"]["input"]["priority"] == 1


@patch("urllib.request.urlopen")
def test_transition_ticket(mock_urlopen, adapter):
    """Test transitioning a ticket."""
    # Mock get (for current state), update (for transition)
    get_response = Mock()
    get_response.read.return_value = json.dumps({
        "data": {
            "searchIssues": {
                "nodes": [
                    {
                        "id": "issue-uuid",
                        "identifier": "DBC-123",
                        "title": "Test",
                        "state": {"id": "old-state", "name": "Backlog"},
                        "priority": 0,
                        "labels": {"nodes": []},
                    }
                ]
            }
        }
    }).encode()
    get_response.__enter__ = Mock(return_value=get_response)
    get_response.__exit__ = Mock(return_value=False)

    search_response = Mock()
    search_response.read.return_value = json.dumps({
        "data": {
            "searchIssues": {
                "nodes": [{"id": "issue-uuid", "identifier": "DBC-123"}]
            }
        }
    }).encode()
    search_response.__enter__ = Mock(return_value=search_response)
    search_response.__exit__ = Mock(return_value=False)

    update_response = Mock()
    update_response.read.return_value = json.dumps({
        "data": {
            "issueUpdate": {
                "success": True,
                "issue": {"id": "issue-uuid", "identifier": "DBC-123"},
            }
        }
    }).encode()
    update_response.__enter__ = Mock(return_value=update_response)
    update_response.__exit__ = Mock(return_value=False)

    mock_urlopen.side_effect = [get_response, search_response, update_response]

    result = adapter.transition("DBC-123", "in_progress")

    assert isinstance(result, TransitionResult)
    assert result.ticket_id == "DBC-123"
    assert result.previous_status == "Backlog"
    assert result.new_status == "in_progress"
    assert result.event_type == "status_changed"


@patch("urllib.request.urlopen")
def test_transition_with_note(mock_urlopen, adapter):
    """Test transition with note (adds comment)."""
    # Mock get, update, and add_comment
    get_response = Mock()
    get_response.read.return_value = json.dumps({
        "data": {
            "searchIssues": {
                "nodes": [
                    {
                        "id": "issue-uuid",
                        "identifier": "DBC-123",
                        "title": "Test",
                        "state": {"id": "old-state", "name": "Backlog"},
                        "priority": 0,
                        "labels": {"nodes": []},
                    }
                ]
            }
        }
    }).encode()
    get_response.__enter__ = Mock(return_value=get_response)
    get_response.__exit__ = Mock(return_value=False)

    search_response = Mock()
    search_response.read.return_value = json.dumps({
        "data": {
            "searchIssues": {
                "nodes": [{"id": "issue-uuid", "identifier": "DBC-123"}]
            }
        }
    }).encode()
    search_response.__enter__ = Mock(return_value=search_response)
    search_response.__exit__ = Mock(return_value=False)

    update_response = Mock()
    update_response.read.return_value = json.dumps({
        "data": {
            "issueUpdate": {
                "success": True,
                "issue": {"id": "issue-uuid", "identifier": "DBC-123"},
            }
        }
    }).encode()
    update_response.__enter__ = Mock(return_value=update_response)
    update_response.__exit__ = Mock(return_value=False)

    # For add_comment - another search, then comment create
    search_response2 = Mock()
    search_response2.read.return_value = json.dumps({
        "data": {
            "searchIssues": {
                "nodes": [{"id": "issue-uuid", "identifier": "DBC-123"}]
            }
        }
    }).encode()
    search_response2.__enter__ = Mock(return_value=search_response2)
    search_response2.__exit__ = Mock(return_value=False)

    comment_response = Mock()
    comment_response.read.return_value = json.dumps({
        "data": {
            "commentCreate": {
                "success": True,
                "comment": {"id": "comment-uuid"},
            }
        }
    }).encode()
    comment_response.__enter__ = Mock(return_value=comment_response)
    comment_response.__exit__ = Mock(return_value=False)

    mock_urlopen.side_effect = [
        get_response,
        search_response,
        update_response,
        search_response2,
        comment_response,
    ]

    result = adapter.transition("DBC-123", "in_progress", note="Starting work")

    # Verify comment was created
    comment_call = mock_urlopen.call_args_list[4]
    body = json.loads(comment_call[0][0].data.decode())
    assert "mutation CreateComment" in body["query"]
    assert body["variables"]["input"]["body"] == "Starting work"


@patch("urllib.request.urlopen")
def test_add_comment(mock_urlopen, adapter):
    """Test adding a comment."""
    search_response = Mock()
    search_response.read.return_value = json.dumps({
        "data": {
            "searchIssues": {
                "nodes": [{"id": "issue-uuid", "identifier": "DBC-123"}]
            }
        }
    }).encode()
    search_response.__enter__ = Mock(return_value=search_response)
    search_response.__exit__ = Mock(return_value=False)

    comment_response = Mock()
    comment_response.read.return_value = json.dumps({
        "data": {
            "commentCreate": {
                "success": True,
                "comment": {"id": "comment-uuid"},
            }
        }
    }).encode()
    comment_response.__enter__ = Mock(return_value=comment_response)
    comment_response.__exit__ = Mock(return_value=False)

    mock_urlopen.side_effect = [search_response, comment_response]

    adapter.add_comment("DBC-123", "Test comment")

    # Verify comment mutation
    comment_call = mock_urlopen.call_args_list[1]
    body = json.loads(comment_call[0][0].data.decode())
    assert "mutation CreateComment" in body["query"]
    assert body["variables"]["input"]["body"] == "Test comment"


@patch("urllib.request.urlopen")
def test_list_tickets(mock_urlopen, adapter):
    """Test listing tickets."""
    mock_response = Mock()
    mock_response.read.return_value = json.dumps({
        "data": {
            "searchIssues": {
                "nodes": [
                    {
                        "id": "issue-1",
                        "identifier": "DBC-123",
                        "title": "Ticket 1",
                        "state": {"id": "state-uuid", "name": "In Progress"},
                        "priority": 2,
                        "labels": {"nodes": []},
                    },
                    {
                        "id": "issue-2",
                        "identifier": "DBC-124",
                        "title": "Ticket 2",
                        "state": {"id": "state-uuid", "name": "In Progress"},
                        "priority": 1,
                        "labels": {"nodes": []},
                    },
                ]
            }
        }
    }).encode()
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)
    mock_urlopen.return_value = mock_response

    tickets = adapter.list_tickets(status="in_progress")

    assert len(tickets) == 2
    assert all(isinstance(t, TicketRecord) for t in tickets)
    assert tickets[0].id == "DBC-123"
    assert tickets[1].id == "DBC-124"

    # Verify search query
    call_args = mock_urlopen.call_args
    body = json.loads(call_args[0][0].data.decode())
    assert body["variables"]["term"] == "status:in_progress"


@patch("urllib.request.urlopen")
def test_list_tickets_multiple_filters(mock_urlopen, adapter):
    """Test listing tickets with multiple filters."""
    mock_response = Mock()
    mock_response.read.return_value = json.dumps({
        "data": {"searchIssues": {"nodes": []}}
    }).encode()
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)
    mock_urlopen.return_value = mock_response

    adapter.list_tickets(
        status="in_progress",
        assignee="john",
        project="q1-goals",
        query="bug",
    )

    # Verify all filters are in query
    call_args = mock_urlopen.call_args
    body = json.loads(call_args[0][0].data.decode())
    query = body["variables"]["term"]
    assert "status:in_progress" in query
    assert "assignee:john" in query
    assert "project:q1-goals" in query
    assert "bug" in query


@patch("urllib.request.urlopen")
def test_graphql_error_handling(mock_urlopen, adapter):
    """Test GraphQL error handling."""
    mock_response = Mock()
    mock_response.read.return_value = json.dumps({
        "errors": [
            {"message": "Authentication failed"},
            {"message": "Invalid query"},
        ]
    }).encode()
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=False)
    mock_urlopen.return_value = mock_response

    with pytest.raises(Exception, match="Linear GraphQL error.*Authentication failed.*Invalid query"):
        adapter.get("DBC-123")


def test_parse_ticket_priority_mapping(adapter):
    """Test that priority values are correctly mapped."""
    test_cases = [
        (0, TicketPriority.NONE),
        (1, TicketPriority.URGENT),
        (2, TicketPriority.HIGH),
        (3, TicketPriority.NORMAL),
        (4, TicketPriority.LOW),
        (99, TicketPriority.NONE),  # Invalid priority defaults to NONE
    ]

    for priority_value, expected_priority in test_cases:
        issue = {
            "id": "test-id",
            "identifier": "DBC-123",
            "title": "Test",
            "state": {"id": "state-id", "name": "Backlog"},
            "priority": priority_value,
            "labels": {"nodes": []},
        }
        ticket = adapter._parse_ticket(issue)
        assert ticket.priority == expected_priority


def test_event_type_determination():
    """Test transition event type logic."""
    adapter = LinearTicketAdapter(api_key="test", team_id="test")

    # Normal transition
    with patch.object(adapter, "get") as mock_get, patch.object(adapter, "update") as mock_update:
        mock_get.return_value = TicketRecord(
            id="DBC-123",
            title="Test",
            status="Backlog",
        )
        adapter._resolve_state_id = lambda x: "state-uuid"

        result = adapter.transition("DBC-123", "in_progress")
        assert result.event_type == "status_changed"

    # Blocked transition
    with patch.object(adapter, "get") as mock_get, patch.object(adapter, "update") as mock_update:
        mock_get.return_value = TicketRecord(
            id="DBC-123",
            title="Test",
            status="In Progress",
        )
        adapter._resolve_state_id = lambda x: "state-uuid"

        result = adapter.transition("DBC-123", "blocked")
        assert result.event_type == "blocked"

    # Unblocked transition
    with patch.object(adapter, "get") as mock_get, patch.object(adapter, "update") as mock_update:
        mock_get.return_value = TicketRecord(
            id="DBC-123",
            title="Test",
            status="blocked",
        )
        adapter._resolve_state_id = lambda x: "state-uuid"

        result = adapter.transition("DBC-123", "in_progress")
        assert result.event_type == "unblocked"
