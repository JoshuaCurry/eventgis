# EventGIS

New EMF mapping toolchain. Very WIP currently.

```
  export EVENTGIS_DB="postgresql://user:pass@host/db"
  uv run ./eventgis.py
```

Make sure to have powerplan python module and spec files available in your eventgis folder. (there is probably a better way to do this)
```
  https://github.com/emfcamp/powerplan
  https://github.com/emfcamp/powerspec
```

To Run PowerPlan
```
  uv run eventgis.py power run
```
