/**
 * Shared Agent Rail width bounds.
 *
 * Both the pre-hydration boot script in app/layout.tsx and the interactive
 * rail in app/components/agent-rail.tsx must clamp persisted widths to the
 * same range; duplicating the literals let them drift apart.
 */
export const AGENT_RAIL_MIN_WIDTH = 320;
export const AGENT_RAIL_DEFAULT_WIDTH = 340;
export const AGENT_RAIL_MAX_WIDTH = 840;
export const AGENT_RAIL_MAX_VIEWPORT_RATIO = 0.68;
