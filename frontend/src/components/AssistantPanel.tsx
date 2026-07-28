import { useState } from "react";
import { Send, Loader2 } from "lucide-react";
import type { ChatResponse, ShipmentChatRequest } from "../api/types";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatResponse["sources"];
}

interface AssistantPanelProps {
  shipmentId: string;
}

export function AssistantPanel({ shipmentId }: AssistantPanelProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: input,
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);
    setError(null);

    try {
      const payload: ShipmentChatRequest = {
        question: userMessage.content,
        conversation_id: conversationId,
      };

      const res = await fetch(`/api/v1/assistant/shipments/${shipmentId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        throw new Error(`API returned ${res.status}`);
      }

      const data: ChatResponse = await res.json();
      
      if (!conversationId) {
        setConversationId(data.conversation_id);
      }

      const assistantMessage: Message = {
        id: data.message_id,
        role: "assistant",
        content: data.answer,
        sources: data.sources,
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="assistant-panel" style={{ marginTop: "2rem", border: "1px solid #ccc", borderRadius: "8px", padding: "1rem" }}>
      <h3 style={{ marginTop: 0 }}>Shipment Assistant</h3>
      <div className="assistant-messages" style={{ maxHeight: "400px", overflowY: "auto", marginBottom: "1rem", display: "flex", flexDirection: "column", gap: "1rem" }}>
        {messages.length === 0 ? (
          <p className="muted">Ask a question about this shipment...</p>
        ) : (
          messages.map((msg) => (
            <div key={msg.id} style={{ alignSelf: msg.role === "user" ? "flex-end" : "flex-start", maxWidth: "80%", backgroundColor: msg.role === "user" ? "#0066cc" : "#f1f1f1", color: msg.role === "user" ? "white" : "black", padding: "0.75rem", borderRadius: "8px" }}>
              <p style={{ margin: 0 }}>{msg.content}</p>
              {msg.sources && msg.sources.length > 0 && (
                <div style={{ marginTop: "0.5rem", fontSize: "0.85rem", opacity: 0.9 }}>
                  <strong>Sources:</strong>
                  <ul style={{ margin: "0.25rem 0 0 0", paddingLeft: "1.25rem" }}>
                    {msg.sources.map((s, i) => (
                      <li key={i}>{s.display_name || s.document_type || "Source"}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ))
        )}
        {error && <p style={{ color: "red", textAlign: "center" }}>{error}</p>}
      </div>
      <form onSubmit={handleSubmit} style={{ display: "flex", gap: "0.5rem" }}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type your question..."
          style={{ flex: 1, padding: "0.5rem", borderRadius: "4px", border: "1px solid #ccc" }}
          disabled={isLoading}
        />
        <button type="submit" disabled={isLoading || !input.trim()} className="button button--primary" style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          {isLoading ? <Loader2 className="spin" size={16} /> : <Send size={16} />}
          Send
        </button>
      </form>
    </div>
  );
}
