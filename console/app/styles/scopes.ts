import common from "./common.module.css";
import shell from "./shell.module.css";
import agent from "./agent.module.css";
import entity from "./entity.module.css";
import research from "./research.module.css";
import monitor from "./monitor.module.css";
import portfolio from "./portfolio.module.css";
import agenda from "./agenda.module.css";
import scorecard from "./scorecard.module.css";
import operations from "./operations.module.css";

// Domain widgets can appear on multiple routes. Their module-owned selectors
// are activated at the Console boundary, never exported as page button skins.
export const consoleScope = [shell.root, agent.root, entity.root, research.root, monitor.root, portfolio.root, agenda.root, scorecard.root, operations.root].join(" ");
export const commonScope = common.root;
export const loginScope = operations.root;
