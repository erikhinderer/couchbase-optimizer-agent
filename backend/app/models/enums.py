from enum import Enum


class ClusterKind(str, Enum):
    ENTERPRISE = "enterprise"
    CAPELLA = "capella"


class ClusterStatus(str, Enum):
    UNKNOWN = "unknown"
    CONNECTED = "connected"
    UNREACHABLE = "unreachable"


class ClusterSourceType(str, Enum):
    # A normal registered cluster the agent connects to over the network via
    # the Couchbase SDK / Management REST, per AccessMode below.
    LIVE = "live"
    # A static, point-in-time snapshot parsed from an uploaded Couchbase
    # support bundle (cbcollect_info output) -- see core/bundle_parser.py and
    # core/bundle_client.py. Always AccessMode.READ_ONLY, enforced
    # server-side regardless of what's stored on the Cluster record, since
    # there is no live connection to apply anything to.
    SUPPORT_BUNDLE = "support_bundle"


class AccessMode(str, Enum):
    # The credentials registered for this cluster are treated as read-only:
    # the agent still analyzes and raises findings (including SAFE_AUTO
    # ones), but approve()/apply() are refused server-side regardless of
    # what the UI shows -- see core/optimizer.py. This is the default for
    # newly registered clusters.
    READ_ONLY = "read_only"
    # The agent may execute approved SAFE_AUTO changes (CREATE/ALTER/DROP
    # INDEX, bucket RAM quota changes) against this cluster. See the
    # README's "Cluster access & permissions" section for the exact
    # Couchbase RBAC roles this requires.
    READ_WRITE = "read_write"


class FindingCategory(str, Enum):
    INDEX = "index"
    QUERY = "query"
    RESOURCE = "resource"
    CONFIGURATION = "configuration"
    STORAGE = "storage"


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ActionType(str, Enum):
    # The agent can perform this safely with no application code change --
    # eligible for the approve-and-apply workflow.
    SAFE_AUTO = "safe_auto"
    # The agent can only suggest this; applying it requires the application's
    # own code/query/schema to change, which the agent will never do itself.
    REQUIRES_CODE_CHANGE = "requires_code_change"


class FindingStatus(str, Enum):
    OPEN = "open"
    SANDBOX_TESTING = "sandbox_testing"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    APPLIED = "applied"
    APPLY_FAILED = "apply_failed"
    REJECTED = "rejected"
    DISMISSED = "dismissed"
    SUGGESTED = "suggested"  # terminal state for REQUIRES_CODE_CHANGE findings


class MemoryTier(str, Enum):
    SHORT_TERM = "short_term"
    EPISODIC = "episodic"
    LONG_TERM = "long_term"
