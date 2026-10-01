## Quick Start
### Template
To build a MonkeFrames extension, we provide a template that will automatically set up your extension for publishing:
```
dotnet new install MonkeFrames.Extensions.Template
```

### Extension.cs
We recommend storing your extension's primary class in `Extension.cs`. Create this file and give it the following content:

```cs
using MonkeFrames.Extensions;

// These parameters should match your listing on extensions.monkeframes.com
[Info("com.myname.myextension", "MyExtension", "1.0.0")]
public class MyExtension : FramesExtension
{
    // This method is called when the extension is first loaded into the game
    public void OnLoad() { }

    // This method is called when the game is closing
    public void OnUnload() { }

    // This method is called before compilation
    public void OnCompilationStart() { }

    // This method is called after compilation
    public void OnCompilationEnd() { }
}
```

For more information on what you can do, see [Builtin Methods](/docs/builtin_methods).