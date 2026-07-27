import { Check, Circle, LoaderCircle } from "lucide-react";

export type StageState = "waiting" | "active" | "complete";

export interface ProcessingStage {
  label: string;
  description: string;
  state: StageState;
}

export function ProcessingTimeline({
  stages,
}: {
  stages: ProcessingStage[];
}) {
  return (
    <ol className="processing-timeline">
      {stages.map((stage) => {
        const Icon =
          stage.state === "complete"
            ? Check
            : stage.state === "active"
              ? LoaderCircle
              : Circle;
        return (
          <li key={stage.label} className={`stage stage--${stage.state}`}>
            <span className="stage__marker">
              <Icon aria-hidden="true" size={15} />
            </span>
            <div>
              <strong>{stage.label}</strong>
              <span>{stage.description}</span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
