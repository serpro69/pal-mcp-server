"""Client guidance for explicit Jules service actions."""

JULES_PROMPT = (
    "Manage optional Jules background coding sessions: list_sources, create, status. "
    "Requires JULES_API_KEY, not a model. Select a repository source and branch explicitly before create. "
    "Plan approval defaults on; auto_create_pr must be explicitly enabled to create remote PRs. "
    "Status returns one activity page and next_page_token; poll with separate calls. "
    "Never repeat a timed-out create blindly: reconcile the remote session in Jules first."
)
