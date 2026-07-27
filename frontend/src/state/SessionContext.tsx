import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type SetStateAction,
  type ReactNode,
} from "react";
import type { ComplianceStatus, TrackedDocument } from "../api/types";

const STORAGE_KEY = "cace.trackedDocuments.v1";

function loadDocuments(): TrackedDocument[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as TrackedDocument[]) : [];
  } catch {
    return [];
  }
}

interface SessionContextValue {
  documents: TrackedDocument[];
  addDocument: (document: TrackedDocument) => void;
  updateDocument: (
    documentId: string,
    patch: Partial<TrackedDocument>,
  ) => void;
  markCompliance: (
    documentIds: string[],
    status: ComplianceStatus,
  ) => void;
  clearDocuments: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [documents, setDocuments] = useState<TrackedDocument[]>(loadDocuments);

  const persist = useCallback((action: SetStateAction<TrackedDocument[]>) => {
    setDocuments((current) => {
      const next =
        typeof action === "function" ? action(current) : action;
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const addDocument = useCallback(
    (document: TrackedDocument) => {
      persist((current) => [
        document,
        ...current.filter((item) => item.id !== document.id),
      ]);
    },
    [persist],
  );

  const updateDocument = useCallback(
    (documentId: string, patch: Partial<TrackedDocument>) => {
      persist((current) =>
        current.map((document) =>
          document.id === documentId ? { ...document, ...patch } : document,
        ),
      );
    },
    [persist],
  );

  const markCompliance = useCallback(
    (documentIds: string[], status: ComplianceStatus) => {
      persist((current) =>
        current.map((document) =>
          documentIds.includes(document.id)
            ? { ...document, complianceStatus: status }
            : document,
        ),
      );
    },
    [persist],
  );

  const clearDocuments = useCallback(() => persist([]), [persist]);

  const value = useMemo(
    () => ({
      documents,
      addDocument,
      updateDocument,
      markCompliance,
      clearDocuments,
    }),
    [
      documents,
      addDocument,
      updateDocument,
      markCompliance,
      clearDocuments,
    ],
  );

  return (
    <SessionContext.Provider value={value}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("useSession must be used inside SessionProvider");
  }
  return value;
}
