# EventGIS

New EMF mapping toolchain. Very WIP currently.

```
  export EVENTGIS_DB="postgresql://user:pass@host/db"
  uv run ./eventgis.py
```

## Power Plan

Make sure to have powerplan python module and spec files available in your eventgis folder. (there is probably a better way to do this)
```
  Clone https://github.com/emfcamp/powerplan and move ./powerplan/powerplan to ./eventgis/powerplan
  Clone https://github.com/emfcamp/powerspec and move ./powerspec to ./eventgis/spec
  Rename .env.example to .env and give it a database connection string
```

To Run PowerPlan
```
  uv run eventgis.py power run
```
