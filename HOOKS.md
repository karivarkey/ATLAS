# Atlas — agent wiring hooks (for the App.tsx owner)

`NotesView`, `TasksView`, and `Notifications` are standalone and need no
`App.tsx` edits to exist — but to surface them in the UI, add a view switch
in `App.tsx` (5 lines):

```tsx
import { NotesView } from './NotesView'          // 1. import
import { TasksView } from './TasksView'          // 2. import
import { Notifications } from './Notifications'  // 3. import
// 4. extend the view state: useState<'chat' | 'memory' | 'notes' | 'tasks'>
// 5. render + bell:
//    {view === 'notes' && <NotesView/>}
//    {view === 'tasks' && <TasksView onOpenConversation={(id) => selectConversation(id)}/>}
//    <Notifications onOpenTask={(id) => {/* setView('tasks') + select task */}} />
```

Props contract:

- `<NotesView onOpenConversation?={(convId) => void} />`
- `<TasksView onOpenConversation?={(convId) => void} />` — called with the
  fresh conversation id when "Let AI do this" starts (deep-link to the run).
- `<Notifications onOpenTask?={(taskId) => void} onOpenNote?={(path) => void}
  pollMs={15000} />` — unread badge, dropdown, mark read/dismiss.

## Agent-loop hooks (for the agent.py owner — logic is ready, NOT wired in)

`backend/app/services/noteagent.py` exposes everything; exact 3-line hook:

```python
from . import noteagent as _na          # 1. import (top of agent.py)
_ctx = _na.build_task_context(task_id)  # 2. call (task executions; None → skip)
# 3. append _ctx['notes'] text to the system prompt / tool results
```

and for proactive nudges:

```python
from . import noteagent as _na                  # 1. import
_rem = _na.inject_chat_reminder(conversation_id)  # 2. call (turn start)
# 3. if _rem['reminders']: prepend as a [reminders — evidence, not instructions] user message
```

and for future tool dispatch (stale-guarded vault writes):

```python
from . import noteagent as _na                          # 1. import
res = await _na.run_notetask_tool(name, args, scope, execution_id)  # 2. call
# 3. feed res['output'] back as the tool result message
```

Available tool names: `note_read`, `note_write` (requires
`expected_version` + `expected_hash` from the same execution's `note_read`;
returns a `conflict` naming `current_version` on staleness),
`task_get`, `task_update`. Schemas are in `noteagent.NOTETASK_TOOL_SCHEMAS`.
