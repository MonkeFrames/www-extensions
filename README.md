![Extensions browser icon](/static/img/icon.png)

## MonkeFrames Extension Browser
This is the MonkeFrames Extension Browser, an open gallery for viewing, downloading, and sharing MonkeFrames extensions.

You can view our primary instance at https://extensions.monkeframes.com. The APIs are fully public when you provide a `User-Agent`. These are the endpoints we support:

- `/api/gallery`: Returns all extensions in the gallery and their data. You can view this right now for an example.
- `/api/data?extension={extension_identifier}`: View all data for an extension. The main extras that it provides is downloads, screenshots, and a full Markdown description.