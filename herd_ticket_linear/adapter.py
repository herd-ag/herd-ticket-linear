"""Linear ticket adapter implementation."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

from herd_core.types import TicketPriority, TicketRecord, TransitionResult

LINEAR_API_URL = "https://api.linear.app/graphql"


class LinearTicketAdapter:
    """Ticket adapter for Linear project management.

    Implements the TicketAdapter protocol from herd-core.
    """

    def __init__(
        self,
        api_key: str,
        team_id: str = "",
        state_mapping: dict[str, str] | None = None,
    ) -> None:
        """Initialize Linear adapter.

        Args:
            api_key: Linear API key (Bearer token).
            team_id: Default team ID for ticket operations.
            state_mapping: Map logical status names to Linear state UUIDs.
                          Example: {"in_progress": "77631f63-b27b-45a5-8b04-f9f82b4facde"}
        """
        self.api_key = api_key
        self.team_id = team_id
        self.state_mapping = state_mapping or {}

    def _graphql_request(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a GraphQL request against Linear API.

        Args:
            query: GraphQL query or mutation string.
            variables: Optional variables for the query.

        Returns:
            Parsed JSON response from Linear API.

        Raises:
            Exception: If request fails or API returns errors.
        """
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            LINEAR_API_URL,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read())

                if "errors" in result:
                    error_msg = "; ".join(
                        e.get("message", str(e)) for e in result["errors"]
                    )
                    raise Exception(f"Linear GraphQL error: {error_msg}")

                return result
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else "No error body"
            raise Exception(f"Linear API HTTP error {e.code}: {error_body}") from e
        except urllib.error.URLError as e:
            raise Exception(f"Linear API network error: {e.reason}") from e

    def _resolve_state_id(self, status: str) -> str:
        """Resolve a status name to a Linear state UUID.

        Args:
            status: Status name (e.g., "in_progress") or UUID.

        Returns:
            Linear state UUID.
        """
        # If it's already a UUID, return as-is
        if re.match(r"^[0-9a-f-]{36}$", status):
            return status

        # Otherwise look up in state_mapping
        if status in self.state_mapping:
            return self.state_mapping[status]

        raise ValueError(
            f"Status '{status}' not found in state_mapping and is not a UUID"
        )

    def _parse_ticket(self, issue: dict[str, Any]) -> TicketRecord:
        """Parse Linear issue into TicketRecord.

        Args:
            issue: Linear issue dict from GraphQL response.

        Returns:
            Parsed TicketRecord.
        """
        # Parse priority (Linear: 0=None, 1=Urgent, 2=High, 3=Normal, 4=Low)
        priority_value = issue.get("priority", 0)
        priority_map = {
            0: TicketPriority.NONE,
            1: TicketPriority.URGENT,
            2: TicketPriority.HIGH,
            3: TicketPriority.NORMAL,
            4: TicketPriority.LOW,
        }
        priority = priority_map.get(priority_value, TicketPriority.NONE)

        # Parse labels
        labels = [label["name"] for label in issue.get("labels", {}).get("nodes", [])]

        # Parse timestamps
        created_at = None
        modified_at = None
        if created_str := issue.get("createdAt"):
            created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        if updated_str := issue.get("updatedAt"):
            modified_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))

        return TicketRecord(
            id=issue.get("identifier", issue.get("id", "")),
            title=issue.get("title", ""),
            description=issue.get("description"),
            status=issue.get("state", {}).get("name", ""),
            priority=priority,
            project=issue.get("project", {}).get("name") if issue.get("project") else None,
            assignee=issue.get("assignee", {}).get("name") if issue.get("assignee") else None,
            labels=labels,
            created_at=created_at,
            modified_at=modified_at,
        )

    def get(self, ticket_id: str) -> TicketRecord:
        """Fetch current state of a ticket.

        Args:
            ticket_id: Linear issue identifier (e.g., 'DBC-120').

        Returns:
            TicketRecord with current ticket state.

        Raises:
            Exception: If ticket not found or API call fails.
        """
        query = """
        query IssueSearch($query: String!) {
          issueSearch(query: $query) {
            nodes {
              id
              identifier
              title
              description
              createdAt
              updatedAt
              state {
                id
                name
              }
              priority
              team {
                id
                name
              }
              project {
                id
                name
              }
              assignee {
                id
                name
              }
              labels {
                nodes {
                  id
                  name
                }
              }
            }
          }
        }
        """

        result = self._graphql_request(query, {"query": ticket_id})
        nodes = result.get("data", {}).get("issueSearch", {}).get("nodes", [])

        # Find exact match on identifier
        for node in nodes:
            if node.get("identifier") == ticket_id:
                return self._parse_ticket(node)

        raise Exception(f"Ticket {ticket_id} not found")

    def create(
        self,
        title: str,
        *,
        description: str | None = None,
        team_id: str | None = None,
        project_id: str | None = None,
        priority: int | None = None,
        labels: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        """Create a new ticket.

        Args:
            title: Ticket title.
            description: Optional ticket description.
            team_id: Team ID (uses default if not provided).
            project_id: Optional project ID.
            priority: Priority level (0=None, 1=Urgent, 2=High, 3=Normal, 4=Low).
            labels: Optional list of label IDs.
            **kwargs: Additional fields (e.g., state_id, assignee_id).

        Returns:
            Created ticket identifier (e.g., 'DBC-123').

        Raises:
            Exception: If creation fails.
        """
        mutation = """
        mutation CreateIssue($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue {
              id
              identifier
              title
              state {
                id
                name
              }
            }
          }
        }
        """

        issue_input: dict[str, Any] = {
            "teamId": team_id or self.team_id,
            "title": title,
        }

        if description:
            issue_input["description"] = description
        if priority is not None:
            issue_input["priority"] = priority
        if project_id:
            issue_input["projectId"] = project_id
        if labels:
            issue_input["labelIds"] = labels

        # Add any additional kwargs (e.g., stateId, assigneeId)
        for key, value in kwargs.items():
            if value is not None:
                issue_input[key] = value

        result = self._graphql_request(mutation, {"input": issue_input})

        data = result.get("data", {}).get("issueCreate", {})
        if not data.get("success"):
            raise Exception("Failed to create Linear issue")

        issue = data.get("issue", {})
        return issue.get("identifier", issue.get("id", ""))

    def update(self, ticket_id: str, **fields: Any) -> None:
        """Update ticket fields.

        Args:
            ticket_id: Linear issue identifier (e.g., 'DBC-120').
            **fields: Fields to update (title, description, priority, labels, etc.).

        Raises:
            Exception: If update fails or ticket not found.
        """
        mutation = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue {
              id
              identifier
            }
          }
        }
        """

        # Map field names to Linear API names
        issue_input: dict[str, Any] = {}

        if "title" in fields:
            issue_input["title"] = fields["title"]
        if "description" in fields:
            issue_input["description"] = fields["description"]
        if "priority" in fields:
            issue_input["priority"] = fields["priority"]
        if "labels" in fields:
            issue_input["labelIds"] = fields["labels"]
        if "state_id" in fields:
            issue_input["stateId"] = fields["state_id"]
        if "assignee_id" in fields:
            issue_input["assigneeId"] = fields["assignee_id"]
        if "project_id" in fields:
            issue_input["projectId"] = fields["project_id"]

        # Get issue ID from the search result
        query = """
        query IssueSearch($query: String!) {
          issueSearch(query: $query) {
            nodes {
              id
              identifier
            }
          }
        }
        """
        result = self._graphql_request(query, {"query": ticket_id})
        nodes = result.get("data", {}).get("issueSearch", {}).get("nodes", [])

        issue_id = None
        for node in nodes:
            if node.get("identifier") == ticket_id:
                issue_id = node.get("id")
                break

        if not issue_id:
            raise Exception(f"Ticket {ticket_id} not found")

        result = self._graphql_request(
            mutation,
            {
                "id": issue_id,
                "input": issue_input,
            },
        )

        data = result.get("data", {}).get("issueUpdate", {})
        if not data.get("success"):
            raise Exception(f"Failed to update ticket {ticket_id}")

    def transition(
        self,
        ticket_id: str,
        to_status: str,
        *,
        note: str | None = None,
        blocked_by: list[str] | None = None,
    ) -> TransitionResult:
        """Transition a ticket to a new status.

        Args:
            ticket_id: Linear issue identifier (e.g., 'DBC-120').
            to_status: Target status name or UUID.
            note: Optional note explaining the transition.
            blocked_by: Ticket IDs that block this one.

        Returns:
            TransitionResult with previous/new status and elapsed time.

        Raises:
            Exception: If transition fails.
        """
        # Get current state
        current_ticket = self.get(ticket_id)
        previous_status = current_ticket.status

        # Resolve state ID
        state_id = self._resolve_state_id(to_status)

        # Update the issue state
        self.update(ticket_id, state_id=state_id)

        # Add comment if note provided
        if note:
            self.add_comment(ticket_id, note)

        # Determine event type
        event_type = "status_changed"
        if to_status == "blocked" or blocked_by:
            event_type = "blocked"
        elif previous_status == "blocked" and to_status != "blocked":
            event_type = "unblocked"

        # Calculate elapsed time (we don't track this in Linear, so return None)
        elapsed_minutes = None

        return TransitionResult(
            ticket_id=ticket_id,
            previous_status=previous_status,
            new_status=to_status,
            event_type=event_type,
            elapsed_minutes=elapsed_minutes,
        )

    def add_comment(self, ticket_id: str, body: str) -> None:
        """Add a comment to a ticket.

        Args:
            ticket_id: Linear issue identifier (e.g., 'DBC-120').
            body: Comment body (markdown supported).

        Raises:
            Exception: If comment creation fails.
        """
        # Get issue ID
        query = """
        query IssueSearch($query: String!) {
          issueSearch(query: $query) {
            nodes {
              id
              identifier
            }
          }
        }
        """
        result = self._graphql_request(query, {"query": ticket_id})
        nodes = result.get("data", {}).get("issueSearch", {}).get("nodes", [])

        issue_id = None
        for node in nodes:
            if node.get("identifier") == ticket_id:
                issue_id = node.get("id")
                break

        if not issue_id:
            raise Exception(f"Ticket {ticket_id} not found")

        mutation = """
        mutation CreateComment($input: CommentCreateInput!) {
          commentCreate(input: $input) {
            success
            comment {
              id
            }
          }
        }
        """

        result = self._graphql_request(
            mutation,
            {
                "input": {
                    "issueId": issue_id,
                    "body": body,
                }
            },
        )

        data = result.get("data", {}).get("commentCreate", {})
        if not data.get("success"):
            raise Exception(f"Failed to add comment to ticket {ticket_id}")

    def list_tickets(self, **filters: Any) -> list[TicketRecord]:
        """List tickets matching filters.

        Args:
            **filters: Filter criteria (status, assignee, project, query, etc.).

        Returns:
            List of matching TicketRecords.
        """
        # Build search query
        query_parts = []

        if "status" in filters:
            query_parts.append(f"status:{filters['status']}")
        if "assignee" in filters:
            query_parts.append(f"assignee:{filters['assignee']}")
        if "project" in filters:
            query_parts.append(f"project:{filters['project']}")
        if "query" in filters:
            query_parts.append(filters["query"])

        search_query = " ".join(query_parts) if query_parts else "*"

        graphql_query = """
        query IssueSearch($query: String!) {
          issueSearch(query: $query) {
            nodes {
              id
              identifier
              title
              description
              createdAt
              updatedAt
              state {
                id
                name
              }
              priority
              team {
                id
                name
              }
              project {
                id
                name
              }
              assignee {
                id
                name
              }
              labels {
                nodes {
                  id
                  name
                }
              }
            }
          }
        }
        """

        result = self._graphql_request(graphql_query, {"query": search_query})
        nodes = result.get("data", {}).get("issueSearch", {}).get("nodes", [])

        return [self._parse_ticket(node) for node in nodes]
