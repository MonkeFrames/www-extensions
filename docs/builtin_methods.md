## Built-in Methods
MonkeFrames provides a number of useful methods you can use in your extension to implement things such as message boxes, custom windows, menus and other systems.

### Called in `OnLoad()`
These methods **must** be called inside of `OnLoad()` in order for them to work.

#### `CreateMenu(string path, Action callback) -> void`
Creates menu buttons automatically so you can bind actions to menu buttons. For example, we create a button that says "hello world" when it is activated:
```cs
public void OnLoad()
{
    // Only supports single paths, you can append to
    // currently existing menus (like File)
    // or create your own
    CreateMenu("Misc/Say Hello World", OnHelloPressed);
}

public void OnHelloPressed()
{
    ShowMessageBox("From the extension", "Hello world!");
}
```

#### `CreateWindow(string windowName, int sizeX, int sizeY, Action onOpen = null!, Action onClose = null!, Action onDraw = null!) -> FramesWindow`


### Misc
Here are some other useful functions which do not directly interact with MonkeFrames (most of which are for file picking, saving, and user input):
```cs
/// <summary>
/// Display a message box and wait for it to close.
/// </summary>
/// <param name="title">The caption displayed above the message box.</param>
/// <param name="body">The body text of the message box.</param>
/// <param name="buttons">The buttons displayed on the message box.</param>
/// <param name="icon">The icon shown on the message box. This will also play a sound based on icons.</param>
/// <returns>The user action performed on the message box. If X is pressed, it will return `MessageBoxResult.Cancel`.</returns>
public static MessageBoxResult ShowMessageBox(string title, string body, MessageBoxButtons buttons = MessageBoxButtons.OK, MessageBoxIcon icon = MessageBoxIcon.None);

/// <summary>
/// Display an Open File dialog to the user and return the path selected, or null if cancelled.
/// </summary>
/// <param name="title">The title of the window.</param>
/// <param name="filter">The file filter to pass, formatted as \"Display Name|*.extension;Other Display Name|*.otherextension\"</param>
/// <param name="initialDirectory">The directory that shows by default when opening the dialog.</param>
/// <param name="flags">Flags to pass to the Windows shell.</param>
/// <returns>Path to the selected file, or null if cancelled.</returns>
public static string ShowOpenFileDialog(string title = "Select File", string filter = "All Files (*.*)|*.*", string initialDirectory = @"C:\", FileDialogFlags flags = FileDialogFlags.OFN_FILEMUSTEXIST);

/// <summary>
/// Display a save file dialog to the user and return the path selected, or null if cancelled.
/// </summary>
/// <param name="title">The title of the window.</param>
/// <param name="filter">The file filter to pass, formatted as \"Display Name|*.extension;Other Display Name|*.otherextension\"</param>
/// <param name="initialDirectory">The directory that shows by default when opening the dialog.</param>
/// <param name="defaultExtension">The extension automatically appended to the file name if none is provided.</param>
/// <param name="flags">Flags to pass to the Windows shell.</param>
/// <returns>Path to the selected file, or null if cancelled.</returns>
public static string ShowSaveFileDialog(string title = "Select File", string filter = "All Files (*.*)|*.*", string initialDirectory = @"C:\", string defaultExtension = "txt", FileDialogFlags flags = FileDialogFlags.OFN_OVERWRITEPROMPT | FileDialogFlags.OFN_PATHMUSTEXIST);
```