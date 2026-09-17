"use client";

import React, { useState } from "react";
import { ListTodo, CheckCircle2, Circle, Clock, ChevronDown, ChevronRight } from "lucide-react";
import { TodoItem } from "@/types/chat";

interface TodoBlockProps {
  todos: TodoItem[];
}

export function TodoBlock({ todos }: TodoBlockProps) {
  const [isOpen, setIsOpen] = useState(true);
  const completedCount = todos.filter((t) => t.status === "completed").length;

  return (
    <div className="my-2 rounded-xl border border-border/80 bg-muted/30 overflow-hidden text-xs">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-3 py-2 bg-muted/50 hover:bg-muted/70 transition-colors font-medium text-foreground"
      >
        <div className="flex items-center gap-2">
          <ListTodo className="size-4 text-primary" />
          <span>Execution Plan / Task Checklist</span>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-background border border-border text-muted-foreground font-mono">
            {completedCount}/{todos.length} Done
          </span>
        </div>
        {isOpen ? <ChevronDown className="size-3.5 text-muted-foreground" /> : <ChevronRight className="size-3.5 text-muted-foreground" />}
      </button>

      {isOpen && (
        <div className="p-2.5 space-y-1.5 border-t border-border/50">
          {todos.map((todo) => {
            const isDone = todo.status === "completed";
            const isInProgress = todo.status === "in_progress";
            return (
              <div key={todo.id} className="flex items-center gap-2 px-2 py-1 rounded hover:bg-muted/40 transition-colors">
                {isDone ? (
                  <CheckCircle2 className="size-3.5 text-emerald-500 shrink-0" />
                ) : isInProgress ? (
                  <Clock className="size-3.5 text-amber-500 animate-spin shrink-0" />
                ) : (
                  <Circle className="size-3.5 text-muted-foreground shrink-0" />
                )}
                <span className={`text-xs ${isDone ? "line-through text-muted-foreground" : "text-foreground"}`}>
                  {todo.title}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
