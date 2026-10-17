"""Job application automation.

Packages:
  core       engine, flows, store, control (portal-independent)
  matching   title and experience filters (rules in config/portals/<name>.toml)
  portals    adapters plus the registry (indeed, linkedin)
  browser    Chrome session and launcher
  app        dashboard wiring for every registered portal
  ui         local dashboard
"""
