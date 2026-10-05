## Quick Start
### Template
To build a MonkeFrames extension, we provide a template that will automatically set up your extension for publishing:
```
dotnet new install MonkeFrames.Extensions.Template
```

### Extension.cs
We recommend storing your extension's primary class in `Extension.cs`. Create this file and give it the following content:

```cs
using MonkeFrames.Compiler;
using MonkeFrames.Extensions;

// These parameters should match your listing on extensions.monkeframes.com
[Info("com.myname.myextension", "MyExtension", "1.0.0")]
public class MyExtension : FramesExtension
{
    // This method is called when the extension is first loaded into the game
    public override void OnLoad() { }

    // This method is called when the game is closing
    public override void OnUnload() { }

    // This method is called before compilation
    public override void OnCompilationStart(Project project) { }

    // This method is called after compilation
    public override void OnCompilationEnd(Project project) { }

    // This method is called before a keyframe is moved to.
    public override void OnKeyframeStep(ref Keyframe keyframe) { }
}
```

For more information on what you can do, see [Builtin Methods](/docs/builtin_methods).

### Harmony Patches
The best way to make changes is via the `ApplyPatch` method, which is able to patch MonkeFrames methods via. Harmony. This is the recommended way to patch methods using extensions due to its simple nature and usage. Like usual, begin all patches in `OnLoad()`. MonkeFrames will automatically unpatch your methods during unloading.

```cs
public override void OnLoad()
{
    // overrides https://github.com/MonkeFrames/MonkeFrames/blob/dev/MonkeFrames.Editor/Utilities/HEncodeUtilities.cs#L7
    PatchMethod("MonkeFrames.Editor.Utilities.HEncodeUtilities:GetGoodEncoder",
        PrefixGetGoodEncoder, patchType: PatchType.Prefix);
}

// __result overrides the return result
public bool PrefixGetGoodEncoder(ref string __result)
{
    __result = "h264_nvenc"; // change return type
    return false; // skip execution of original
}
```