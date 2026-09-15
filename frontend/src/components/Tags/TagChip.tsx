import type { Tag } from "../../types";
import { fallbackColor } from "../../constants";

interface Props {
  name: string;
  tags: Tag[]; // registry, for color lookup
  onRemove?: () => void;
}

/** Colored label chip. Color comes from the tag registry, else a stable hash. */
export function TagChip({ name, tags, onRemove }: Props) {
  const registered = tags.find((t) => t.name === name);
  const color = registered?.color ?? fallbackColor(name);
  return (
    <span className="tag-chip" style={{ background: color }} title={name}>
      {name}
      {onRemove && (
        <span
          className="remove"
          role="button"
          aria-label={`Remove tag ${name}`}
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
        >
          ×
        </span>
      )}
    </span>
  );
}
