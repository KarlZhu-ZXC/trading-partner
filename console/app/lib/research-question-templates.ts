export type ResearchQuestionTemplate = Readonly<{
  id: string;
  version: number;
  title: string;
  question: string;
  evidence_requirements: readonly string[];
}>;

/**
 * Local, versioned prompts reduce repetitive drafting without creating a
 * Candidate or filling any fact, source, deadline, or Decision field.
 */
export const RESEARCH_QUESTION_TEMPLATES: readonly ResearchQuestionTemplate[] = [
  {
    id: "earnings_review",
    version: 1,
    title: "Earnings Review",
    question: "What did the latest earnings report change about the assumptions behind the active Thesis?",
    evidence_requirements: [
      "Latest reported revenue, margin, and cash-flow facts are available for the relevant period.",
      "The reported facts are comparable with the Thesis assumption's basis and time window.",
      "Any variance from the assumption is explained by a durable or temporary factor.",
    ],
  },
  {
    id: "competitive_position",
    version: 1,
    title: "Competitive Position",
    question: "What evidence shows that the Research Subject's competitive position is strengthening, stable, or weakening?",
    evidence_requirements: [
      "At least one current customer, product, market-share, or competitor fact is available.",
      "The evidence is dated and scoped to this Research Subject.",
      "The conclusion distinguishes an observed change from an unverified interpretation.",
    ],
  },
  {
    id: "thesis_invalidation",
    version: 1,
    title: "Thesis Invalidation",
    question: "What observable fact would falsify the active Thesis, and does current evidence meet that condition?",
    evidence_requirements: [
      "The falsification condition is stated as an observable fact or threshold.",
      "Current evidence is checked against the exact condition and time window.",
      "Any missing or conflicting source is recorded before concluding that invalidation occurred.",
    ],
  },
] as const;

export function getResearchQuestionTemplate(id: string): ResearchQuestionTemplate | null {
  return RESEARCH_QUESTION_TEMPLATES.find((template) => template.id === id) ?? null;
}

export function applyResearchQuestionTemplate(template: ResearchQuestionTemplate): { text: string; evidenceRequirements: string } {
  return {
    text: template.question,
    evidenceRequirements: template.evidence_requirements.join("\n"),
  };
}
