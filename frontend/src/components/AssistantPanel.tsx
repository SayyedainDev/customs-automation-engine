import { useState, KeyboardEvent, useRef, useEffect } from "react";
import { Send, Loader2, BookOpen, AlertCircle, FileText, CheckCircle2, FileSearch } from "lucide-react";
import type { ChatResponse, ShipmentChatRequest } from "../api/types";
import { api } from "../api/client";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatResponse["sources"];
  answer_type?: string;
}

interface AssistantPanelProps {
  shipmentId: string;
}

const ANSWER_TYPE_LABELS: Record<string, string> = {
  document_fact: "Document fact",
  audit_result: "Audit result",
  regulatory_guidance: "Regulatory guidance",
  combined_answer: "Combined answer",
  out_of_scope: "Out of scope",
  clarification: "Clarification",
};

export function AssistantPanel({ shipmentId }: AssistantPanelProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([
    "What is the invoice total?",
    "Do the invoice and packing list match?",
    "Why did this shipment pass?",
    "Which document is missing?"
  ]);
  const [limitations, setLimitations] = useState<string[]>([]);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submitQuestion(input);
    }
  };

  const submitQuestion = async (question: string) => {
    if (!question.trim() || isLoading) return;

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: question,
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);
    setError(null);

    try {
      const payload: ShipmentChatRequest = {
        question: question,
        conversation_id: conversationId,
      };

      const data = await api.sendChat(shipmentId, payload);
      
      if (!conversationId) {
        setConversationId(data.conversation_id);
      }

      if (data.limitations && data.limitations.length > 0) {
        setLimitations(data.limitations);
      }

      if (data.suggested_questions && data.suggested_questions.length > 0) {
        setSuggestedQuestions(data.suggested_questions);
      }

      const assistantMessage: Message = {
        id: data.message_id,
        role: "assistant",
        content: data.answer,
        sources: data.sources,
        answer_type: data.answer_type,
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void submitQuestion(input);
  };

  return (
    <div className="assistant-panel" style={{ marginTop: "2rem", border: "1px solid #ccc", borderRadius: "8px", padding: "1.5rem", backgroundColor: "#fff" }}>
      <h3 style={{ marginTop: 0, display: "flex", alignItems: "center", gap: "0.5rem" }}>
        <FileSearch size={20} />
        Shipment Assistant
      </h3>
      
      <div className="assistant-messages" style={{ maxHeight: "500px", overflowY: "auto", marginBottom: "1rem", display: "flex", flexDirection: "column", gap: "1.5rem", padding: "1rem", backgroundColor: "#fafafa", borderRadius: "8px", border: "1px solid #eee" }}>
        {messages.length === 0 ? (
          <div style={{ textAlign: "center", padding: "2rem", color: "#666" }}>
            <p>Ask a question about this shipment's documents, audit results, or regulations.</p>
            <p style={{fontSize: "0.9rem"}}>Chat cannot modify audit data. To correct values, use the formal human-review workflow above.</p>
          </div>
        ) : (
          messages.map((msg) => (
            <div key={msg.id} style={{ alignSelf: msg.role === "user" ? "flex-end" : "flex-start", maxWidth: "85%" }}>
              
              <div style={{ 
                backgroundColor: msg.role === "user" ? "#0066cc" : "#ffffff", 
                color: msg.role === "user" ? "white" : "black", 
                padding: "1rem", 
                borderRadius: "8px",
                border: msg.role === "assistant" ? "1px solid #ddd" : "none",
                boxShadow: "0 1px 2px rgba(0,0,0,0.05)"
              }}>
                {msg.role === "assistant" && msg.answer_type && (
                  <div style={{ marginBottom: "0.75rem" }}>
                    <span className="status-chip status-chip--neutral" style={{fontSize: "0.75rem", padding: "0.2rem 0.5rem"}}>
                      {ANSWER_TYPE_LABELS[msg.answer_type] || msg.answer_type}
                    </span>
                  </div>
                )}
                
                <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{msg.content}</div>
                
                {msg.sources && msg.sources.length > 0 && (
                  <div style={{ marginTop: "1.5rem", borderTop: "1px solid #eee", paddingTop: "1rem" }}>
                    <strong style={{ fontSize: "0.85rem", textTransform: "uppercase", color: "#666" }}>Sources</strong>
                    <ul className="citation-list" style={{ marginTop: "0.5rem" }}>
                      {msg.sources.map((s, i) => (
                        <li key={i} className="citation-card" style={{ marginBottom: "0.5rem" }}>
                          <div className="citation-card__head">
                            <BookOpen size={14} />
                            <strong>{s.display_name || s.document_name || "Source"}</strong>
                            {s.page_number && <span className="muted">page {s.page_number}</span>}
                          </div>
                          <div className="citation-card__provenance">
                            <span className={`source-kind-chip source-kind-chip--${s.source_kind === 'regulatory' ? 'official' : s.source_kind === 'audit_finding' ? 'curated' : 'neutral'}`}>
                              {s.source_kind.replace('_', ' ')}
                            </span>
                            {s.audit_revision_number && <span className="muted">revision {s.audit_revision_number}</span>}
                            {s.evidence_status && <span className="muted">status: {s.evidence_status}</span>}
                          </div>
                          {s.snippet && <p className="citation-card__snippet">“{s.snippet}”</p>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
        {error && (
          <div className="notice notice--danger">
            <AlertCircle size={16} />
            <p>{error}</p>
          </div>
        )}
      </div>

      {suggestedQuestions.length > 0 && messages.length === 0 && (
        <div style={{ marginBottom: "1rem", display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          {suggestedQuestions.map((sq, i) => (
            <button 
              key={i} 
              type="button" 
              className="button button--secondary" 
              style={{ fontSize: "0.85rem", padding: "0.25rem 0.75rem", borderRadius: "16px" }}
              onClick={() => void submitQuestion(sq)}
              disabled={isLoading}
            >
              {sq}
            </button>
          ))}
        </div>
      )}

      {limitations.length > 0 && (
        <div style={{ marginBottom: "1rem", fontSize: "0.8rem", color: "#666" }}>
          {limitations.map((limit, i) => (
            <div key={i} style={{ display: "flex", gap: "0.25rem", alignItems: "flex-start", marginBottom: "0.25rem" }}>
              <AlertCircle size={12} style={{ marginTop: "0.15rem", flexShrink: 0 }} />
              <span>{limit}</span>
            </div>
          ))}
        </div>
      )}

      <form onSubmit={handleSubmit} style={{ display: "flex", gap: "0.75rem", alignItems: "flex-end" }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about this shipment (Press Enter to send, Shift+Enter for newline)..."
          style={{ flex: 1, padding: "0.75rem", borderRadius: "6px", border: "1px solid #ccc", minHeight: "2.5rem", maxHeight: "150px", resize: "vertical", fontFamily: "inherit" }}
          disabled={isLoading}
          rows={1}
        />
        <button type="submit" disabled={isLoading || !input.trim()} className="button button--primary" style={{ padding: "0.75rem 1.5rem" }}>
          {isLoading ? <Loader2 className="spin" size={18} /> : <Send size={18} />}
          <span>Send</span>
        </button>
      </form>
    </div>
  );
}
