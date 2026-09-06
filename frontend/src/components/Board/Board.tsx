import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import type { Tag, Task, TaskStatus, ViewMode } from "../../types";
import { ME, UNASSIGNED } from "../../constants";
import { personLaneOrder, workstreamLaneOrder } from "../../utils";
import { Swimlane } from "./Swimlane";

interface Props {
  tasks: Task[]; // already filtered
  tags: Tag[];
  viewMode: ViewMode;
  workstreams: string[];
  roster: string[];
  onStatusChange: (id: string, status: TaskStatus) => void;
  onDueChange: (id: string, due: string | null) => void;
  onTagsChange: (id: string, tags: string[]) => void;
  onDelete: (id: string) => void;
  onSend: (task: Task) => void;
  onCreateTag: (name: string) => Promise<void> | void;
}

export function Board({
  tasks,
  tags,
  viewMode,
  workstreams,
  roster,
  onStatusChange,
  onDueChange,
  onTagsChange,
  onDelete,
  onSend,
  onCreateTag,
}: Props) {
  // Require a small drag distance so a click on the handle doesn't misfire.
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }));

  const laneKey = viewMode === "person" ? "assignee" : "workstream";
  const lanes =
    viewMode === "person"
      ? personLaneOrder(tasks, roster)
      : workstreamLaneOrder(tasks, workstreams);

  const tasksForLane = (lane: string) =>
    tasks.filter((t) => (viewMode === "person" ? t.assignee : t.workstream) === lane);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over) return;
    const targetStatus = over.data.current?.status as TaskStatus | undefined;
    const sourceStatus = active.data.current?.status as TaskStatus | undefined;
    if (targetStatus && targetStatus !== sourceStatus) {
      onStatusChange(String(active.id), targetStatus);
    }
  };

  const visibleLanes = lanes.filter((lane) => {
    const laneTasks = tasksForLane(lane);
    // Always show ME and non-unassigned configured lanes; hide empty extras.
    if (laneTasks.length > 0) return true;
    if (viewMode === "person") return lane === ME || roster.includes(lane);
    return lane !== UNASSIGNED && workstreams.includes(lane);
  });

  return (
    <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
      <div className="board">
        {visibleLanes.length === 0 && (
          <div className="empty-state">No tasks match the current filters.</div>
        )}
        {visibleLanes.map((lane) => (
          <Swimlane
            key={`${laneKey}:${lane}`}
            label={lane === ME ? `${lane} (me)` : lane}
            tasks={tasksForLane(lane)}
            tags={tags}
            // ME lane and workstream lanes open by default; teammate/unassigned collapsed.
            defaultOpen={viewMode === "workstream" ? lane !== UNASSIGNED : lane === ME}
            onStatusChange={onStatusChange}
            onDueChange={onDueChange}
            onTagsChange={onTagsChange}
            onDelete={onDelete}
            onSend={onSend}
            onCreateTag={onCreateTag}
          />
        ))}
      </div>
    </DndContext>
  );
}
