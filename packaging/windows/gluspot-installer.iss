; Current-user Windows installer. Only shipped binaries are replaced on upgrade.
; The sibling GluSpot state directory is retained until an explicit uninstall.
#ifndef AppVersion
  #define AppVersion "6.414"
#endif
#ifndef PayloadDir
  #error PayloadDir must point to a completed GluSpot onedir build
#endif
#ifndef ArtifactDir
  #define ArtifactDir "..\..\release"
#endif
#ifndef InstallerAssets
  #error InstallerAssets must point to prepare-installer-assets.py output
#endif
#ifndef InstallerAppId
  #define InstallerAppId "GluSpot-A61F6E83-E49E-4A99-9660-306235631B73"
#endif
#ifndef InstallRegistryKey
  #define InstallRegistryKey "Software\GluSpot\Install"
#endif
#ifndef InstallGroupName
  #define InstallGroupName "GluSpot"
#endif
#ifndef PayloadMinVersion
  #define PayloadMinVersion "10.0.17763"
#endif
#ifndef InstallerSuffix
  #define InstallerSuffix ""
#endif

[Setup]
AppId={#InstallerAppId}
AppName=GluSpot
AppVersion={#AppVersion}
AppPublisher=GluSpot
AppVerName=GluSpot {#AppVersion}
DefaultDirName={localappdata}\Programs\GluSpot
DefaultGroupName={#InstallGroupName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; The installer engine can run on Win7; the modern payload cannot. The
; separately built Qt5 legacy installer explicitly supplies 6.1sp1 here.
MinVersion={#PayloadMinVersion}
UninstallDisplayName=GluSpot
UninstallDisplayIcon={app}\GluSpot.exe
SetupIconFile=..\..\assets\brand\gluspot-glu.ico
OutputDir={#ArtifactDir}
OutputBaseFilename=GluSpot-{#AppVersion}{#InstallerSuffix}-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=classic
WizardBackColor=#ffffff
WizardBackColorDynamicDark=#ffffff
WizardSizePercent=100
DisableWelcomePage=yes
DisableReadyPage=yes
DisableDirPage=no
DisableProgramGroupPage=yes
AllowNoIcons=yes
UsePreviousAppDir=yes
CloseApplications=no
RestartApplications=no
SetupLogging=yes
UninstallLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupWindowTitle=GluSpot 安装
WelcomeLabel1=让音乐留在身边。
WelcomeLabel2=欢迎使用 GluSpot。%n%n选择安装目录后即可开始。配置、账号与缓存保留在程序目录内；更新时会保留这些信息。%n%n完整卸载会移除本目录内的 GluSpot 信息。
ClickNext=点击“继续”选择安装位置。
SelectDirLabel3=选择 GluSpot 的专用安装目录。
SelectDirBrowseLabel=程序与配置保存在同一目录。请选择可写的独立文件夹。
ButtonNext=继续(&N)
ButtonBack=返回(&B)
ButtonInstall=安装(&I)
ButtonCancel=取消
ButtonFinish=完成
ButtonBrowse=浏览(&B)...
SelectDirDesc=选择安装位置
ReadyLabel1=准备安装 GluSpot
FinishedHeadingLabel=GluSpot 已准备就绪
#ifdef LegacyWindows7
FinishedLabel=此兼容版仅启用程序内播放。Windows 7 不支持本程序使用的 SMTC 系统同步。
#else
FinishedLabel=首次启动将选择播放方式。默认同时开启程序内播放与 Windows SMTC 系统同步。
#endif
StatusExtractFiles=正在安装 GluSpot...
ConfirmUninstall=确定完整卸载 GluSpot 吗？%n%n程序目录内的配置、账号与缓存也会清除。

[Files]
Source: "{#InstallerAssets}\compact-*.png"; Flags: dontcopy
Source: "{#PayloadDir}\GluSpot.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\GluSpot"

[INI]
Filename: "{app}\gluspot-install.ini"; Section: "GluSpot"; Key: "InstallPath"; String: "{app}"
Filename: "{app}\gluspot-install.ini"; Section: "GluSpot"; Key: "Version"; String: "{#AppVersion}"

[UninstallDelete]
Type: files; Name: "{app}\gluspot-install.ini"

[Icons]
Name: "{group}\GluSpot"; Filename: "{app}\GluSpot.exe"
Name: "{group}\卸载 GluSpot"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "{#InstallRegistryKey}"; ValueType: string; ValueName: "Path"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "{#InstallRegistryKey}"; ValueType: string; ValueName: "Version"; ValueData: "{#AppVersion}"; Flags: uninsdeletekey

[Code]
const
  FileAttributeReparsePoint = $400;
  InvalidFileAttributes = $FFFFFFFF;
  SceneMargin = 24;
type
  TPointerPoint = record
    X, Y: Integer;
  end;
  TLayerSize = record
    Width, Height: LongInt;
  end;
  TBlendData = record
    Operation, Flags, Alpha, Format: Byte;
  end;
  TDIBHeader = record
    Size: LongWord;
    Width, Height: LongInt;
    Planes, BitCount: Word;
    Compression, ImageSize: LongWord;
    XPelsPerMeter, YPelsPerMeter: LongInt;
    ColorsUsed, ColorsImportant: LongWord;
  end;
  TGdiStartup = record
    Version: LongWord;
    DebugCallback: NativeUInt;
    SuppressBackgroundThread, SuppressExternalCodecs: LongWord;
  end;
  TFloatRect = record
    X, Y, Width, Height: Single;
  end;
  TCodecGuid = record
    Data1: LongWord;
    Data2, Data3: Word;
    Data4: array [0..7] of Byte;
  end;
  TMonitorData = record
    Size: LongWord;
    MonitorRect: TRect;
    WorkRect: TRect;
    Flags: LongWord;
  end;
var
  PaletteTimer: UINT_PTR;
  AnimationStartedTick, CompletionStartedTick, FinishBrandLoopTick, HandoffStartedTick: DWORD;
  BrandFrames: array [0..299] of NativeUInt;
  LowerFrames: array [0..123] of NativeUInt;
  LowerOutFrames: array [0..143] of NativeUInt;
  SloganFrames: array [0..30] of NativeUInt;
  FinishFrames: array [0..39] of NativeUInt;
  CloseFrames, MinimizeFrames, ControlCapsuleFrames: array [0..30] of NativeUInt;
  PrimaryButton, DirectoryButton, CloseButton, MinimizeButton: TBitmapButton;
  InstallerSubclassCallback: NativeInt;
  InstallerSubclassInstalled, DirectoryManagerOpen: Boolean;
  DirectoryManager: TSetupForm;
  DirectoryPathEdit: TNewEdit;
  DirectoryStatus: TNewStaticText;
  DirectoryRegistered: String;
  GdiToken, LayerImage, LayerGraphics, LayerBits, LayerDIB, OldLayerDIB: NativeUInt;
  LayerDC: HDC;
  LayerWidth, LayerHeight, PresentedHeight: Integer;
  SelectedBrand, SelectedLower, SelectedSlogan: NativeUInt;
  IslandX, IslandY, IslandWidth, IslandHeight: Double;
  ProgressValue: Integer;
  ProgressOpacity: Double;
  ShadowOpacity: Double;
  ShowBrand, ShowLower, ShowProgress, ShowSlogan: Boolean;
  FontFamily, DirectoryFont, PrimaryFont, PathFormat: NativeUInt;
  SceneFontName: String;
  NativeFrameCount: Integer;
  CustomPageID, LastBrandFrame, LastLowerFrame, CurrentMode, ModeOffset: Integer;
  SceneReady, PaintingScene, CompletionRunning, HandoffLaunched, TimerResolutionHeld: Boolean;
  HandoffRevealed, HandoffFailed: Boolean;
  HandoffReadyEvent, HandoffRevealEvent, HandoffPaintedEvent: THandle;
  HandoffToken: String;
  StoppedRunningTarget, RestartAttempted: Boolean;
  OriginalWindowRect: TRect;
  FinalLeft, FinalTop, FinalWidth, FinalHeight, LastBodyHeight: Integer;
  LastCompositionTick: DWORD;
  TimerTicked: Boolean;
  LastPointerPoint: TPointerPoint;
  LastPointerMovementTick, LastControlTick: DWORD;
  ControlReveal: Double;
  LastControlFrame: Integer;
#ifdef VisualDiagnosticDir
  DiagnosticMask: Integer;
  DiagnosticFrameIndex: Integer;
  DiagnosticLastFrameTick: DWORD;
  DiagnosticControlsMask: Integer;
#endif
  ApprovedTarget: String;
  RequestedSilentTargetError: String;
  BackupPath: String;
  BackupReady, InstallCompleted: Boolean;

function GetFileAttributesW(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function GetTickCount: DWORD;
  external 'GetTickCount@kernel32.dll stdcall';
function SetTimer(Wnd: HWND; ID: UINT_PTR; Elapse: UINT; Callback: NativeInt): UINT_PTR;
  external 'SetTimer@user32.dll stdcall';
function KillTimer(Wnd: HWND; ID: UINT_PTR): Boolean;
  external 'KillTimer@user32.dll stdcall';
function CreateFileW(Name: String; Access, Share: LongWord; Security: LongWord;
  Creation, Flags, Template: LongWord): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function CreateEventW(Attributes: LongWord; ManualReset, InitialState: Boolean; Name: String): THandle;
  external 'CreateEventW@kernel32.dll stdcall';
function SetEvent(Handle: THandle): Boolean;
  external 'SetEvent@kernel32.dll stdcall';

function EnumProcesses(var FirstPID: LongWord; BufferSize: LongWord; var Bytes: LongWord): Boolean;
  external 'EnumProcesses@psapi.dll stdcall';
function OpenProcess(Access: LongWord; Inherit: Boolean; PID: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function QueryFullProcessImageNameW(Process: THandle; Flags: LongWord; Name: String; var Size: LongWord): Boolean;
  external 'QueryFullProcessImageNameW@kernel32.dll stdcall';
function GetFinalPathNameByHandleW(Handle: THandle; Name: String; Size, Flags: LongWord): LongWord;
  external 'GetFinalPathNameByHandleW@kernel32.dll stdcall';
function TerminateProcess(Process: THandle; ExitCode: LongWord): Boolean;
  external 'TerminateProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function GetCurrentProcessId: LongWord;
  external 'GetCurrentProcessId@kernel32.dll stdcall';
function CreateEllipticRgn(Left, Top, Right, Bottom: Integer): THandle;
  external 'CreateEllipticRgn@gdi32.dll stdcall';
function SetWindowRgn(Window: HWND; Region: THandle; Redraw: Boolean): Integer;
  external 'SetWindowRgn@user32.dll stdcall';
function DeleteObject(Obj: THandle): Boolean;
  external 'DeleteObject@gdi32.dll stdcall';
function CreateCompatibleDC(DC: HDC): HDC;
  external 'CreateCompatibleDC@gdi32.dll stdcall';
function DeleteDC(DC: HDC): Boolean;
  external 'DeleteDC@gdi32.dll stdcall';
function SelectObject(DC: HDC; Obj: NativeUInt): NativeUInt;
  external 'SelectObject@gdi32.dll stdcall';
function CreateDIBSection(DC: HDC; var Info: TDIBHeader; Usage: LongWord;
  var Bits: NativeUInt; Section: THandle; Offset: LongWord): NativeUInt;
  external 'CreateDIBSection@gdi32.dll stdcall';
function UpdateLayeredWindow(Window: HWND; ScreenDC: HDC; var Dest: TPointerPoint;
  var Size: TLayerSize; SourceDC: HDC; var Source: TPointerPoint; Color: LongWord;
  var Blend: TBlendData; Flags: LongWord): Boolean;
  external 'UpdateLayeredWindow@user32.dll stdcall';
function GdiplusStartup(var Token: NativeUInt; var Input: TGdiStartup; Output: NativeUInt): Integer;
  external 'GdiplusStartup@gdiplus.dll stdcall';
procedure GdiplusShutdown(Token: NativeUInt);
  external 'GdiplusShutdown@gdiplus.dll stdcall';
function GdipCreateBitmapFromScan0(Width, Height, Stride, Format: Integer; Scan: NativeUInt; var Image: NativeUInt): Integer;
  external 'GdipCreateBitmapFromScan0@gdiplus.dll stdcall';
function GdipCreateBitmapFromFile(Name: String; var Image: NativeUInt): Integer;
  external 'GdipCreateBitmapFromFile@gdiplus.dll stdcall';
function GdipDisposeImage(Image: NativeUInt): Integer;
  external 'GdipDisposeImage@gdiplus.dll stdcall';
function GdipGetImageGraphicsContext(Image: NativeUInt; var Graphics: NativeUInt): Integer;
  external 'GdipGetImageGraphicsContext@gdiplus.dll stdcall';
function GdipDeleteGraphics(Graphics: NativeUInt): Integer;
  external 'GdipDeleteGraphics@gdiplus.dll stdcall';
function GdipGraphicsClear(Graphics: NativeUInt; Color: LongWord): Integer;
  external 'GdipGraphicsClear@gdiplus.dll stdcall';
function GdipSetSmoothingMode(Graphics: NativeUInt; Mode: Integer): Integer;
  external 'GdipSetSmoothingMode@gdiplus.dll stdcall';
function GdipSetInterpolationMode(Graphics: NativeUInt; Mode: Integer): Integer;
  external 'GdipSetInterpolationMode@gdiplus.dll stdcall';
function GdipSetPixelOffsetMode(Graphics: NativeUInt; Mode: Integer): Integer;
  external 'GdipSetPixelOffsetMode@gdiplus.dll stdcall';
function GdipSetTextRenderingHint(Graphics: NativeUInt; Hint: Integer): Integer;
  external 'GdipSetTextRenderingHint@gdiplus.dll stdcall';
function GdipDrawImageRect(Graphics, Image: NativeUInt; X, Y, Width, Height: Single): Integer;
  external 'GdipDrawImageRect@gdiplus.dll stdcall';
function GdipCreatePath(Mode: Integer; var Path: NativeUInt): Integer;
  external 'GdipCreatePath@gdiplus.dll stdcall';
function GdipAddPathArc(Path: NativeUInt; X, Y, Width, Height, Start, Sweep: Single): Integer;
  external 'GdipAddPathArc@gdiplus.dll stdcall';
function GdipClosePathFigure(Path: NativeUInt): Integer;
  external 'GdipClosePathFigure@gdiplus.dll stdcall';
function GdipDeletePath(Path: NativeUInt): Integer;
  external 'GdipDeletePath@gdiplus.dll stdcall';
function GdipCreateSolidFill(Color: LongWord; var Brush: NativeUInt): Integer;
  external 'GdipCreateSolidFill@gdiplus.dll stdcall';
function GdipDeleteBrush(Brush: NativeUInt): Integer;
  external 'GdipDeleteBrush@gdiplus.dll stdcall';
function GdipFillPath(Graphics, Brush, Path: NativeUInt): Integer;
  external 'GdipFillPath@gdiplus.dll stdcall';
function GdipCreateFontFamilyFromName(Name: String; Collection: NativeUInt; var Family: NativeUInt): Integer;
  external 'GdipCreateFontFamilyFromName@gdiplus.dll stdcall';
function GdipGetGenericFontFamilySansSerif(var Family: NativeUInt): Integer;
  external 'GdipGetGenericFontFamilySansSerif@gdiplus.dll stdcall';
function GdipDeleteFontFamily(Family: NativeUInt): Integer;
  external 'GdipDeleteFontFamily@gdiplus.dll stdcall';
function GdipCreateFont(Family: NativeUInt; Size: Single; Style, UnitKind: Integer; var Font: NativeUInt): Integer;
  external 'GdipCreateFont@gdiplus.dll stdcall';
function GdipDeleteFont(Font: NativeUInt): Integer;
  external 'GdipDeleteFont@gdiplus.dll stdcall';
function GdipStringFormatGetGenericTypographic(var Format: NativeUInt): Integer;
  external 'GdipStringFormatGetGenericTypographic@gdiplus.dll stdcall';
function GdipSetStringFormatFlags(Format: NativeUInt; Flags: Integer): Integer;
  external 'GdipSetStringFormatFlags@gdiplus.dll stdcall';
function GdipSetStringFormatTrimming(Format: NativeUInt; Trimming: Integer): Integer;
  external 'GdipSetStringFormatTrimming@gdiplus.dll stdcall';
function GdipDeleteStringFormat(Format: NativeUInt): Integer;
  external 'GdipDeleteStringFormat@gdiplus.dll stdcall';
function GdipDrawString(Graphics: NativeUInt; Text: String; Length: Integer; Font: NativeUInt;
  var Layout: TFloatRect; Format, Brush: NativeUInt): Integer;
  external 'GdipDrawString@gdiplus.dll stdcall';
function GetWindowLongPtrW(Window: HWND; Index: Integer): NativeInt;
  external 'GetWindowLongW@user32.dll stdcall';
function SetWindowLongPtrW(Window: HWND; Index: Integer; Value: NativeInt): NativeInt;
  external 'SetWindowLongW@user32.dll stdcall';
function ReleaseCapture: Boolean;
  external 'ReleaseCapture@user32.dll stdcall';
function PostMessageW(Window: HWND; Msg: UINT; WParam: NativeUInt; LParam: NativeInt): Boolean;
  external 'PostMessageW@user32.dll stdcall';
function ShowWindow(Window: HWND; Command: Integer): Boolean;
  external 'ShowWindow@user32.dll stdcall';

function timeBeginPeriod(Period: UINT): UINT;
  external 'timeBeginPeriod@winmm.dll stdcall';
function timeEndPeriod(Period: UINT): UINT;
  external 'timeEndPeriod@winmm.dll stdcall';
function GetWindowRect(Window: HWND; var Bounds: TRect): Boolean;
  external 'GetWindowRect@user32.dll stdcall';
function MonitorFromWindow(Window: HWND; Flags: LongWord): THandle;
  external 'MonitorFromWindow@user32.dll stdcall';
function GetMonitorInfoW(Monitor: THandle; var Info: TMonitorData): Boolean;
  external 'GetMonitorInfoW@user32.dll stdcall';
function SetWindowPos(Window, InsertAfter: HWND; X, Y, Width, Height: Integer; Flags: LongWord): Boolean;
  external 'SetWindowPos@user32.dll stdcall';

function SetWindowSubclass(Window: HWND; Callback: NativeInt; ID: UINT_PTR; RefData: NativeUInt): Boolean;
  external 'SetWindowSubclass@comctl32.dll stdcall';
function RemoveWindowSubclass(Window: HWND; Callback: NativeInt; ID: UINT_PTR): Boolean;
  external 'RemoveWindowSubclass@comctl32.dll stdcall';
function DefSubclassProc(Window: HWND; Msg: UINT; WParam: NativeUInt; LParam: NativeInt): NativeInt;
  external 'DefSubclassProc@comctl32.dll stdcall';
function IsWindowEnabled(Window: HWND): Boolean;
  external 'IsWindowEnabled@user32.dll stdcall';
function GdipFlush(Graphics: NativeUInt; Intention: Integer): Integer;
  external 'GdipFlush@gdiplus.dll stdcall';

#ifdef VisualDiagnosticDir
function GdipSaveImageToFile(Image: NativeUInt; FileName: String; var Codec: TCodecGuid; Params: NativeUInt): Integer;
  external 'GdipSaveImageToFile@gdiplus.dll stdcall';
function WriteFile(Handle: THandle; Buffer: NativeUInt; Bytes: LongWord; var Written: LongWord; Overlapped: NativeUInt): Boolean;
  external 'WriteFile@kernel32.dll stdcall';

function IsIconic(Window: HWND): Boolean;
  external 'IsIconic@user32.dll stdcall';
function IsZoomed(Window: HWND): Boolean;
  external 'IsZoomed@user32.dll stdcall';
function SendMessageW(Window: HWND; Msg: UINT; WParam: NativeUInt; LParam: NativeInt): NativeInt;
  external 'SendMessageW@user32.dll stdcall';

procedure ExportLayerFrame(Name: String; Raw: Boolean);
var
  Codec: TCodecGuid;
  OutputFile: THandle;
  Written: LongWord;
  Bounds: TRect;
begin
  Codec.Data1 := $557CF406; Codec.Data2 := $1A04; Codec.Data3 := $11D3;
  Codec.Data4[0] := $9A; Codec.Data4[1] := $73; Codec.Data4[2] := 0; Codec.Data4[3] := 0;
  Codec.Data4[4] := $F8; Codec.Data4[5] := $1E; Codec.Data4[6] := $F3; Codec.Data4[7] := $2E;
  if GdipSaveImageToFile(LayerImage, '{#VisualDiagnosticDir}\' + Name + '.png', Codec, 0) <> 0 then
    RaiseException('Native layered PNG export failed');
  if Raw then begin
    OutputFile := CreateFileW('{#VisualDiagnosticDir}\' + Name + '.bgra', $40000000, 0, 0, 2, 0, 0);
    if OutputFile = -1 then RaiseException('Native DIB export failed');
    try
      if not WriteFile(OutputFile, LayerBits, LayerWidth * LayerHeight * 4, Written, 0) then
        RaiseException('Native DIB readback failed');
    finally
      CloseHandle(OutputFile);
    end;
  end;
  GetWindowRect(WizardForm.Handle, Bounds);
  SaveStringToFile('{#VisualDiagnosticDir}\frames.tsv', Name + #9 + IntToStr(GetTickCount - AnimationStartedTick) + #9 + IntToStr(Bounds.Left) + #9 + IntToStr(Bounds.Top) + #9 + IntToStr(LayerWidth) + #9 + IntToStr(PresentedHeight) + #13#10, True);
  Log('ULW frame exported from the presented PARGB DIB: ' + Name + ', ' + IntToStr(LayerWidth) + 'x' + IntToStr(PresentedHeight));
end;

procedure ExportNativeFrame(Bit: Integer; Name: String);
begin
  if (DiagnosticMask and Bit) <> 0 then exit;
  DiagnosticMask := DiagnosticMask or Bit;
  ExportLayerFrame(Name, True);
end;
#endif

function GetCursorPos(var Point: TPointerPoint): Boolean;
  external 'GetCursorPos@user32.dll stdcall';
function ScreenToClient(Window: HWND; var Point: TPointerPoint): Boolean;
  external 'ScreenToClient@user32.dll stdcall';
function GetFocus: HWND;
  external 'GetFocus@user32.dll stdcall';

function UiX(Value: Integer): Integer;
begin
#ifdef VisualDiagnosticDPI
  Result := Round(Value * {#VisualDiagnosticDPI} / 96.0);
#else
  Result := ScaleX(Value);
#endif
end;

function UiY(Value: Integer): Integer;
begin
#ifdef VisualDiagnosticDPI
  Result := Round(Value * {#VisualDiagnosticDPI} / 96.0);
#else
  Result := ScaleY(Value);
#endif
end;

procedure UpdateWindowControls(Time: DWORD);
var
  Point: TPointerPoint;
  Inside, Focused: Boolean;
  Target, Response: Double;
  Frame: Integer;
begin
  if CompletionRunning then begin
    CloseButton.Hide; MinimizeButton.Hide; exit;
  end;
#ifdef VisualDiagnosticControls
  // The fixture's pointer trace is fully isolated from the real cursor.
  // Never move user input, or let incidental movement invalidate idle proof.
  Inside := True;
  if (Time - AnimationStartedTick > 300) and (Time - AnimationStartedTick < 1100) then
    LastPointerMovementTick := Time;
#else
  Inside := False;
  if GetCursorPos(Point) and ScreenToClient(WizardForm.Handle, Point) then begin
    Inside := (Point.X >= UiX(SceneMargin)) and (Point.Y >= UiY(SceneMargin)) and
      (Point.X < UiX(SceneMargin + 370)) and (Point.Y < UiY(SceneMargin + LastBodyHeight));
    if Inside and ((Point.X <> LastPointerPoint.X) or (Point.Y <> LastPointerPoint.Y)) then
      LastPointerMovementTick := Time;
    LastPointerPoint := Point;
  end;
#endif
  Focused := (GetFocus = CloseButton.Handle) or (GetFocus = MinimizeButton.Handle);
  if Focused or (Inside and (Time - LastPointerMovementTick < 1400)) then Target := 1 else Target := 0;
  if Target > ControlReveal then Response := (Time - LastControlTick) / 150.0
  else Response := (Time - LastControlTick) / 350.0;
  if Response > 1 then Response := 1;
  ControlReveal := ControlReveal + (Target - ControlReveal) * Response;
  LastControlTick := Time;
  Frame := Round(ControlReveal * 30);
  if (Target = 0) and (Frame = 0) then ControlReveal := 0;
  LastControlFrame := Frame;
  CloseButton.Visible := Frame > 0;
  MinimizeButton.Visible := Frame > 0;
  // At zero opacity no invisible mouse or keyboard hit target remains.
  CloseButton.TabStop := Frame > 0;
  MinimizeButton.TabStop := Frame > 0;
end;

function CanonicalFileName(Name: String): String;
var
  Handle: THandle;
  Count: LongWord;
begin
  Result := '';
  Handle := CreateFileW(Name, 0, 7, 0, 3, $02000000, 0);
  if Handle = THandle(-1) then exit;
  try
    Result := StringOfChar(#0, 32768);
    Count := GetFinalPathNameByHandleW(Handle, Result, 32768, 0);
    if (Count = 0) or (Count >= 32768) then begin Result := ''; exit; end;
    SetLength(Result, Count);
    if Copy(Result, 1, 4) = '\\?\' then Delete(Result, 1, 4);
    Result := Lowercase(Result);
  finally
    CloseHandle(Handle);
  end;
end;

function StopExactTarget(Target: String): String;
var
  PIDs: array [0..8191] of LongWord;
  Bytes, Size, PID: LongWord;
  Index, Count: Integer;
  Process: THandle;
  Expected, ImageName: String;
begin
  Result := '';
  Expected := CanonicalFileName(Target + '\GluSpot.exe');
  if Expected = '' then begin Result := '无法验证现有程序路径，更新已停止。'; exit; end;
  if not EnumProcesses(PIDs[0], 32768, Bytes) or (Bytes >= 32768) then begin
    Result := '无法完整检查正在运行的程序，更新已停止。'; exit;
  end;
  Count := Bytes div 4;
  for Index := 0 to Count - 1 do begin
    PID := PIDs[Index];
    if (PID = 0) or (PID = GetCurrentProcessId) then continue;
    // Keep this handle through the comparison and stop: PID reuse cannot
    // redirect termination to another process. Never match only by EXE name.
    Process := OpenProcess($101001, False, PID);
    if Process = 0 then continue;
    try
      Size := 32768;
      ImageName := StringOfChar(#0, Size);
      if QueryFullProcessImageNameW(Process, 0, ImageName, Size) then begin
        SetLength(ImageName, Size);
        if (CanonicalFileName(ImageName) = Expected) then begin
          if (WaitForSingleObject(Process, 0) <> 0) and
             (not TerminateProcess(Process, 0) or (WaitForSingleObject(Process, 5000) <> 0)) then begin
            Result := '无法关闭此安装目录中的 GluSpot。原文件与配置已保留，请退出后重试。'; exit;
          end;
          StoppedRunningTarget := True;
          Log('Stopped the verified target GluSpot process for replacement: ' + IntToStr(PID));
        end;
      end;
    finally
      CloseHandle(Process);
    end;
  end;
end;

function HasRedirectedParent(Path: String): Boolean;
var
  Attr: LongWord;
  Parent: String;
begin
  Result := True;
  Path := RemoveBackslashUnlessRoot(ExpandFileName(Path));
  while Length(Path) > 0 do begin
    Attr := GetFileAttributesW(Path);
    if (Attr <> InvalidFileAttributes) and ((Attr and FileAttributeReparsePoint) <> 0) then exit;
    Parent := ExtractFileDir(Path);
    if CompareText(Parent, Path) = 0 then break;
    Path := Parent;
  end;
  Result := False;
end;

function TreeHasRedirect(Path: String): Boolean;
var
  Rec: TFindRec;
begin
  Result := HasRedirectedParent(Path);
  if Result then exit;
  if FindFirst(AddBackslash(Path) + '*', Rec) then begin
    try
      repeat
        if (Rec.Name <> '.') and (Rec.Name <> '..') then begin
          if (Rec.Attributes and FileAttributeReparsePoint) <> 0 then begin
            Result := True; exit;
          end;
          if ((Rec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and
             TreeHasRedirect(AddBackslash(Path) + Rec.Name) then begin
            Result := True; exit;
          end;
        end;
      until not FindNext(Rec);
    finally
      FindClose(Rec);
    end;
  end;
end;

function RemoveOwnedTree(Path, Parent: String): Boolean;
begin
  Result := False;
  Path := ExpandFileName(Path);
  Parent := AddBackslash(ExpandFileName(Parent));
  if (CompareText(Copy(Path, 1, Length(Parent)), Parent) <> 0) or
     TreeHasRedirect(Path) then exit;
  if not DirExists(Path) then begin Result := True; exit; end;
  Result := DelTree(Path, True, True, True);
end;

function CompareVersion(First, Second: String): Integer;
var
  I, A, B, PosA, PosB: Integer;
begin
  Result := 0;
  for I := 0 to 3 do begin
    PosA := Pos('.', First); if PosA = 0 then PosA := Length(First) + 1;
    PosB := Pos('.', Second); if PosB = 0 then PosB := Length(Second) + 1;
    A := StrToIntDef(Copy(First, 1, PosA - 1), 0);
    B := StrToIntDef(Copy(Second, 1, PosB - 1), 0);
    if A > B then begin Result := 1; exit; end;
    if A < B then begin Result := -1; exit; end;
    Delete(First, 1, PosA); Delete(Second, 1, PosB);
  end;
end;

function HasOwnedInstallMarker(Target: String): Boolean;
var
  Recorded: String;
begin
  Target := RemoveBackslashUnlessRoot(ExpandFileName(Target));
  Recorded := GetIniString('GluSpot', 'InstallPath', '', Target + '\gluspot-install.ini');
  Result := (CompareText(Recorded, Target) = 0) and
    (GetIniString('GluSpot', 'Version', '', Target + '\gluspot-install.ini') <> '');
end;

function IsRecognizedInstallation(Target: String): Boolean;
var
  Version: String;
begin
  Target := RemoveBackslashUnlessRoot(ExpandFileName(Target));
  Result := HasOwnedInstallMarker(Target) or
    (GetVersionNumbersString(AddBackslash(Target) + 'GluSpot.exe', Version) and
     FileExists(Target + '\_internal\assets\brand\gluspot-glu.ico') and
     FileExists(Target + '\_internal\PySide6\Qt6Core.dll'));
end;

function RegisteredInstallDirectory: String;
var
  Registered: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, '{#InstallRegistryKey}', 'Path', Registered) and
     not HasRedirectedParent(Registered) and IsRecognizedInstallation(Registered) then
    Result := RemoveBackslashUnlessRoot(ExpandFileName(Registered));
end;

function RegisteredTargetError(Target: String): String;
var
  Registered: String;
begin
  Result := '';
  Registered := RegisteredInstallDirectory;
  Target := RemoveBackslashUnlessRoot(ExpandFileName(Target));
  if (Registered <> '') and (CompareText(Registered, Target) <> 0) then
    Result := '已发现 GluSpot 安装于：' + Registered + #13#10#13#10 +
      '为保留配置与账号，请覆盖原安装目录。需要更换目录时，请先完整卸载原版本。';
end;

function ValidInstallTarget(Target: String): Boolean;
var
  Rec: TFindRec;
  Name, Drive: String;
begin
  Target := RemoveBackslashUnlessRoot(ExpandFileName(Target));
  Result := False;
  Drive := AddBackslash(ExtractFileDrive(Target));
  if (Length(Target) < 4) or (CompareText(AddBackslash(Target), Drive) = 0) or
     HasRedirectedParent(Target) or
     (CompareText(Target, RemoveBackslashUnlessRoot(GetEnv('USERPROFILE'))) = 0) or
     (CompareText(Target, ExpandConstant('{localappdata}')) = 0) or
     (CompareText(Target, ExpandConstant('{win}')) = 0) or
     (CompareText(Target, ExpandConstant('{sys}')) = 0) or
     (CompareText(Target, ExpandConstant('{autopf}')) = 0) then exit;
  // The owned marker also permits repairing a missing EXE in its original
  // directory while retaining the sibling GluSpot state directory.
  if IsRecognizedInstallation(Target) then begin Result := True; exit; end;
  Result := True;
  if FindFirst(AddBackslash(Target) + '*', Rec) then begin
    try
      repeat
        Name := Rec.Name;
        if (Name <> '.') and (Name <> '..') then begin Result := False; exit; end;
      until not FindNext(Rec);
    finally
      FindClose(Rec);
    end;
  end;
end;

function ConfirmVersion(Target: String): Boolean;
var
  Existing, Key: String;
begin
  Result := True;
  Existing := '';
  GetVersionNumbersString(AddBackslash(Target) + 'GluSpot.exe', Existing);
  if (Existing = '') and HasOwnedInstallMarker(Target) then
    Existing := GetIniString('GluSpot', 'Version', '', Target + '\gluspot-install.ini');
  if Existing = '' then exit;
  Key := Lowercase(ExpandFileName(Target)) + '|' + Existing;
  if ApprovedTarget = Key then exit;
  if CompareVersion('{#AppVersion}', Existing) > 0 then begin ApprovedTarget := Key; exit; end;
  // A silent run cannot stand in for the user's explicit overwrite choice.
  if WizardSilent then begin Result := False; exit; end;
  Result := MsgBox('已存在 GluSpot ' + Existing + '。当前安装包版本为 {#AppVersion}。' + #13#10#13#10 +
    '是否覆盖安装？程序目录内的配置与账号会保留。选择“否”将返回。', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  if Result then ApprovedTarget := Key;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  TargetError: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    TargetError := RegisteredTargetError(WizardDirValue);
    if TargetError <> '' then begin
      SuppressibleMsgBox(TargetError, mbError, MB_OK, IDOK);
      Result := False; exit;
    end;
    if not ValidInstallTarget(WizardDirValue) then begin
      SuppressibleMsgBox('请选择独立、可写的空目录，或已有的 GluSpot 目录。不能安装到系统目录、已有其他文件的目录或重定向目录。', mbError, MB_OK, IDOK);
      Result := False; exit;
    end;
    Result := ConfirmVersion(WizardDirValue);
  end;
end;

procedure RestoreBackup;
var
  Target: String;
begin
  if not BackupReady then exit;
  Target := ExpandConstant('{app}');
  if DirExists(BackupPath + '\_internal') then begin
    if not RemoveOwnedTree(Target + '\_internal', Target) then exit;
    RenameFile(BackupPath + '\_internal', Target + '\_internal');
  end;
  if FileExists(BackupPath + '\GluSpot.exe') then begin
    DeleteFile(Target + '\GluSpot.exe');
    RenameFile(BackupPath + '\GluSpot.exe', Target + '\GluSpot.exe');
  end;
  RemoveDir(BackupPath);
  BackupReady := False;
end;

#ifdef LegacyWindows7
function LegacyGetModuleHandle(Name: String): THandle;
  external 'GetModuleHandleW@kernel32.dll stdcall';
function LegacyGetProcAddress(Module: THandle; Name: AnsiString): NativeUInt;
  external 'GetProcAddress@kernel32.dll stdcall';

function LegacyRuntimePrerequisiteError: String;
begin
  Result := '';
  if LegacyGetProcAddress(LegacyGetModuleHandle('kernel32.dll'), 'AddDllDirectory') = 0 then begin
    Result := '此 Win7 兼容版需要 DLL 加载系统更新（KB2533623 或其替代更新）。' + #13#10 +
      '请先完成 Windows 系统更新，再运行安装程序。现有程序与配置尚未更改。';
    exit;
  end;
  if not FileExists(ExpandConstant('{sys}\ucrtbase.dll')) then
    Result := '此 Win7 兼容版需要 Universal C Runtime（KB2999226 或其替代更新）。' + #13#10 +
      '请先安装对应的系统运行库更新，再运行安装程序。现有程序与配置尚未更改。';
end;
#endif

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Target: String;
  Handle: THandle;
begin
  Result := '';
#ifdef LegacyWindows7
  Result := LegacyRuntimePrerequisiteError;
  if Result <> '' then exit;
#endif
  Target := ExpandConstant('{app}');
  if RequestedSilentTargetError <> '' then begin
    Result := RequestedSilentTargetError; exit;
  end;
  Result := RegisteredTargetError(Target);
  if Result <> '' then exit;
  if not ValidInstallTarget(Target) then begin Result := '安装目录不可用，请选择 GluSpot 专用目录。'; exit; end;
  if not ConfirmVersion(Target) then begin Result := '已有相同或更高版本。请使用可交互安装并明确选择覆盖。'; exit; end;
  if not FileExists(Target + '\GluSpot.exe') and not DirExists(Target + '\_internal') then exit;
  BackupPath := Target + '\.gluspot-upgrade-backup';
  if DirExists(BackupPath) or FileExists(BackupPath) or TreeHasRedirect(Target + '\_internal') then begin
    Result := '存在待恢复的升级备份或重定向目录。请保留现有文件并先完成恢复。'; exit;
  end;
  if FileExists(Target + '\GluSpot.exe') then begin
    Result := StopExactTarget(Target);
    if Result <> '' then exit;
    Handle := CreateFileW(Target + '\GluSpot.exe', $40000000, 7, 0, 3, 0, 0);
    if Handle = THandle(-1) then begin Result := '程序文件仍被其他进程占用，原文件与配置已保留。'; exit; end;
    CloseHandle(Handle);
  end;
  if not ForceDirectories(BackupPath) then begin Result := '无法创建升级备份。'; exit; end;
  BackupReady := True;
  if DirExists(Target + '\_internal') and not RenameFile(Target + '\_internal', BackupPath + '\_internal') then begin
    Result := '现有运行依赖正在使用中，请正常退出 GluSpot。'; RestoreBackup; exit;
  end;
  if FileExists(Target + '\GluSpot.exe') and not RenameFile(Target + '\GluSpot.exe', BackupPath + '\GluSpot.exe') then begin
    Result := '无法更新程序。已保留原版本，请检查目录权限。'; RestoreBackup;
  end;
end;

// The supplied white-card storyboard owns the visual surface. All brand,
// lower-layout rasters are decoded once before showing it. Actual installation
// progress is a native rounded bar in the directory row, independent of brand.
// Frame callbacks only compose cached images; no disk reads/style invalidation.
function Smooth(Value: Double): Double;
begin
  if Value < 0 then Value := 0;
  if Value > 1 then Value := 1;
  Result := Value * Value * (3 - 2 * Value);
end;

function ClampFrame(Value, Maximum: Integer): Integer;
begin
  Result := Value;
  if Result < 0 then Result := 0;
  if Result > Maximum then Result := Maximum;
end;

procedure GdiCheck(Status: Integer; Operation: String);
begin
  if Status <> 0 then RaiseException('Installer graphics: ' + Operation + ' (' + IntToStr(Status) + ')');
end;

function PixelX(Value: Double): Single;
begin
  Result := Value * UiX(370) / 370.0;
end;

function PixelY(Value: Double): Single;
begin
  Result := Value * UiY(258) / 258.0;
end;

function TrySceneFont(Name: String): Boolean;
var
  Family, RegularFont, ButtonFont: NativeUInt;
  Status: Integer;
  LabelText: String;
begin
  Result := False;
  Family := 0; RegularFont := 0; ButtonFont := 0;
  LabelText := Name;
  if Name = '' then LabelText := 'generic sans serif';
  try
    if Name = '' then
      Status := GdipGetGenericFontFamilySansSerif(Family)
    else
      Status := GdipCreateFontFamilyFromName(Name, 0, Family);
    if Status = 0 then
      Status := GdipCreateFont(Family, PixelY(11), 0, 2, RegularFont);
    if Status = 0 then begin
      Status := GdipCreateFont(Family, PixelY(12), 1, 2, ButtonFont);
      // Some installed families expose regular only. Keep labels readable
      // rather than aborting the wizard or passing an invalid font to GDI+.
      if Status = 15 then begin
        if ButtonFont <> 0 then GdipDeleteFont(ButtonFont);
        ButtonFont := 0;
        Log('Installer UI bold unavailable; using regular: ' + LabelText);
        Status := GdipCreateFont(Family, PixelY(12), 0, 2, ButtonFont);
      end;
    end;
    if (Status <> 0) or (Family = 0) or (RegularFont = 0) or (ButtonFont = 0) then begin
      Log('Installer UI font unavailable: ' + LabelText + ' (' + IntToStr(Status) + ')');
      exit;
    end;
    FontFamily := Family; DirectoryFont := RegularFont; PrimaryFont := ButtonFont;
    Family := 0; RegularFont := 0; ButtonFont := 0;
    SceneFontName := Name;
    Log('Installer UI font selected: ' + LabelText);
    Result := True;
  finally
    if ButtonFont <> 0 then GdipDeleteFont(ButtonFont);
    if RegularFont <> 0 then GdipDeleteFont(RegularFont);
    if Family <> 0 then GdipDeleteFontFamily(Family);
  end;
end;

procedure InitializeSceneFonts;
begin
  // Microsoft YaHei UI first shipped with Windows 8. Win7 supplies YaHei
  // and SimSun; stripped or non-Chinese systems can use the remaining faces.
  if TrySceneFont('Microsoft YaHei UI') then exit;
  if TrySceneFont('Microsoft YaHei') then exit;
  if TrySceneFont('SimSun') then exit;
  if TrySceneFont('Segoe UI') then exit;
  if TrySceneFont('Arial') then exit;
  if TrySceneFont('') then exit;
  RaiseException('无法加载安装界面字体，请检查 Windows 字体组件。');
end;

procedure FillRound(X, Y, Width, Height, Radius: Double; Color: LongWord);
var
  Path, Brush: NativeUInt;
  PX, PY, PW, PH, Diameter: Single;
begin
  PX := PixelX(X + SceneMargin); PY := PixelY(Y + SceneMargin);
  PW := PixelX(Width); PH := PixelY(Height);
  Diameter := PixelX(Radius * 2);
  GdiCheck(GdipCreatePath(0, Path), 'create rounded path');
  GdiCheck(GdipCreateSolidFill(Color, Brush), 'create fill');
  try
    GdipAddPathArc(Path, PX, PY, Diameter, Diameter, 180, 90);
    GdipAddPathArc(Path, PX + PW - Diameter, PY, Diameter, Diameter, 270, 90);
    GdipAddPathArc(Path, PX + PW - Diameter, PY + PH - Diameter, Diameter, Diameter, 0, 90);
    GdipAddPathArc(Path, PX, PY + PH - Diameter, Diameter, Diameter, 90, 90);
    GdipClosePathFigure(Path);
    GdiCheck(GdipFillPath(LayerGraphics, Brush, Path), 'fill rounded path');
  finally
    GdipDeleteBrush(Brush);
    GdipDeletePath(Path);
  end;
end;

procedure DrawRaster(Image: NativeUInt; X, Y, Width, Height: Double);
begin
  if Image <> 0 then
    GdiCheck(GdipDrawImageRect(LayerGraphics, Image, PixelX(X + SceneMargin), PixelY(Y + SceneMargin), PixelX(Width), PixelY(Height)), 'draw cached raster');
end;

procedure DrawText(Text: String; X, Y, Width, Height: Double; Font: NativeUInt; Color: LongWord);
var
  Box: TFloatRect;
  Brush: NativeUInt;
begin
  Box.X := PixelX(X + SceneMargin); Box.Y := PixelY(Y + SceneMargin);
  Box.Width := PixelX(Width); Box.Height := PixelY(Height);
  GdipCreateSolidFill(Color, Brush);
  try
    GdiCheck(GdipDrawString(LayerGraphics, Text, Length(Text), Font, Box, PathFormat, Brush), 'draw directory label');
  finally
    GdipDeleteBrush(Brush);
  end;
end;

procedure DrawInstallProgress;
var
  Alpha: LongWord;
  Width, Radius: Double;
begin
  // Reuse the complete directory/install row without changing the card height.
  // Actual extraction progress controls the width; no timed/raster animation.
  Alpha := Round(255 * ProgressOpacity) * $01000000;
  FillRound(16, 214, 338, 30, 15, Alpha or $00EEF2EF);
  Width := 338 * ProgressValue / 100.0;
  if Width > 0 then begin
    Radius := 15;
    if Width < 30 then Radius := Width / 2;
    FillRound(16, 214, Width, 30, Radius, Alpha or $0055B72C);
  end;
  DrawText('正在安装 GluSpot', 30, 220, 226, 20, PrimaryFont, Alpha or $00213925);
  DrawText(IntToStr(ProgressValue) + '%', 302, 220, 44, 20, PrimaryFont, Alpha or $00213925);
end;

procedure DrawCardShadow(BodyHeight: Integer);
var
  Spread, Alpha: Integer;
begin
  // Per-pixel alpha lives outside the card. Small nested fills provide a soft
  // falloff without DWM dependencies, binary regions or a rectangular fringe.
  if ShadowOpacity <= 0 then exit;
  for Spread := 18 downto 1 do begin
    Alpha := Round((1 + (18 - Spread) / 6.0) * ShadowOpacity);
    if Alpha > 0 then
      FillRound(-Spread, 4 - Spread, 370 + Spread * 2,
        BodyHeight + Spread * 2, 32 + Spread, Alpha * $01000000);
  end;
end;

procedure PresentLayeredScene(BodyHeight: Integer);
var
  Position, Source: TPointerPoint;
  Size: TLayerSize;
  Bounds: TRect;
  Blend: TBlendData;
begin
  GdiCheck(GdipGraphicsClear(LayerGraphics, 0), 'clear PARGB DIB');
  DrawCardShadow(BodyHeight);
  if (IslandWidth >= 370) and (BodyHeight = 64) then
    FillRound(0, 0, 370, BodyHeight, 32, $FF000000)
  else begin
    FillRound(0, 0, 370, BodyHeight, 32, $FFFFFFFF);
    FillRound(IslandX, IslandY, IslandWidth, IslandHeight, IslandHeight / 2, $FF000000);
  end;
  if ShowBrand then DrawRaster(SelectedBrand, 14, 12, 342, 48);
  if ShowProgress then DrawInstallProgress;
  if ShowLower then DrawRaster(SelectedLower, 14, 78, 342, 120);
  if ShowSlogan then DrawRaster(SelectedSlogan, 0, 0, 370, 64);
  if (CustomPageID = wpSelectDir) and not CompletionRunning then begin
    FillRound(16, 214, 236, 30, 15, $FFF3F5F4);
    FillRound(262, 214, 92, 30, 15, $FF55B72C);
    if DirectoryRegistered <> '' then begin
      DrawText('安装位置 · 已安装', 25, 215, 157, 15, DirectoryFont, $FF656B63);
      DrawText('查看…', 198, 215, 46, 15, DirectoryFont, $FF328A2D);
    end else begin
      DrawText('安装位置', 25, 215, 157, 15, DirectoryFont, $FF656B63);
      DrawText('更改…', 198, 215, 46, 15, DirectoryFont, $FF328A2D);
    end;
    DrawText(WizardDirValue, 25, 230, 209, 14, DirectoryFont, $FF656B63);
    DrawText('安装', 287, 220, 60, 20, PrimaryFont, $FFFFFFFF);
    if GetFocus = DirectoryButton.Handle then begin
      FillRound(15, 213, 238, 32, 16, $FF328A2D);
      FillRound(17, 215, 234, 28, 14, $FFF3F5F4);
      DrawText('安装位置 · Enter 查看', 25, 216, 217, 15, DirectoryFont, $FF328A2D);
      DrawText(WizardDirValue, 25, 230, 209, 14, DirectoryFont, $FF656B63);
    end;
    if GetFocus = PrimaryButton.Handle then begin
      FillRound(261, 213, 94, 32, 16, $FF184F23);
      FillRound(263, 215, 90, 28, 14, $FF55B72C);
      DrawText('安装', 287, 220, 60, 20, PrimaryFont, $FFFFFFFF);
    end;
  end;
  if not CompletionRunning and (LastControlFrame > 0) then begin
    DrawRaster(ControlCapsuleFrames[LastControlFrame], 18, 20, 64, 32);
    DrawRaster(CloseFrames[LastControlFrame], 26, 26, 20, 20);
    DrawRaster(MinimizeFrames[LastControlFrame], 54, 26, 20, 20);
    // The accessible native HWNDs keep keyboard input; draw their focus cue
    // into the same frame because child painting is not part of ULW.
    if GetFocus = CloseButton.Handle then begin
      FillRound(25, 25, 22, 22, 11, $FFFFFFFF);
      DrawRaster(CloseFrames[LastControlFrame], 26, 26, 20, 20);
    end;
    if GetFocus = MinimizeButton.Handle then begin
      FillRound(53, 25, 22, 22, 11, $FFFFFFFF);
      DrawRaster(MinimizeFrames[LastControlFrame], 54, 26, 20, 20);
    end;
  end;
  GetWindowRect(WizardForm.Handle, Bounds);
  Position.X := Bounds.Left; Position.Y := Bounds.Top;
  Source.X := 0; Source.Y := 0;
  Size.Width := LayerWidth; Size.Height := Round(PixelY(BodyHeight + SceneMargin * 2));
  Blend.Operation := 0; Blend.Flags := 0; Blend.Alpha := 255; Blend.Format := 1;
  GdiCheck(GdipFlush(LayerGraphics, 1), 'flush shared PARGB surface');
  if not UpdateLayeredWindow(WizardForm.Handle, 0, Position, Size, LayerDC, Source, 0, Blend, 2) then
    RaiseException('Installer could not present its per-pixel-alpha frame');
  LastBodyHeight := BodyHeight;
  PresentedHeight := Size.Height;
  NativeFrameCount := NativeFrameCount + 1;
  if NativeFrameCount = 1 then Log('Native ULW_ALPHA PARGB DIB presentation succeeded: ' + IntToStr(LayerWidth) + 'x' + IntToStr(Size.Height));
end;

procedure InitializeLayeredScene;
var
  Startup: TGdiStartup;
  Header: TDIBHeader;
begin
  Startup.Version := 1; Startup.DebugCallback := 0;
  Startup.SuppressBackgroundThread := 0; Startup.SuppressExternalCodecs := 0;
  GdiCheck(GdiplusStartup(GdiToken, Startup, 0), 'start GDI+');
  LayerWidth := WizardForm.ClientWidth; LayerHeight := WizardForm.ClientHeight;
  Header.Size := 40; Header.Width := LayerWidth; Header.Height := -LayerHeight;
  Header.Planes := 1; Header.BitCount := 32; Header.Compression := 0;
  Header.ImageSize := LayerWidth * LayerHeight * 4;
  Header.XPelsPerMeter := 0; Header.YPelsPerMeter := 0;
  Header.ColorsUsed := 0; Header.ColorsImportant := 0;
  LayerDC := CreateCompatibleDC(0);
  LayerDIB := CreateDIBSection(LayerDC, Header, 0, LayerBits, 0, 0);
  if (LayerDIB = 0) or (LayerBits = 0) then RaiseException('Installer DIB allocation failed');
  OldLayerDIB := SelectObject(LayerDC, LayerDIB);
  // PixelFormat32bppPARGB: GDI+ writes the premultiplied-alpha pixels directly
  // into the exact top-down DIB selected by UpdateLayeredWindow.
  GdiCheck(GdipCreateBitmapFromScan0(LayerWidth, LayerHeight, LayerWidth * 4, $E200B, LayerBits, LayerImage), 'wrap native PARGB DIB');
  GdiCheck(GdipGetImageGraphicsContext(LayerImage, LayerGraphics), 'create frame graphics');
  GdipSetSmoothingMode(LayerGraphics, 4);
  GdipSetInterpolationMode(LayerGraphics, 7);
  GdipSetPixelOffsetMode(LayerGraphics, 4);
  GdipSetTextRenderingHint(LayerGraphics, 4);
  InitializeSceneFonts;
  GdiCheck(GdipStringFormatGetGenericTypographic(PathFormat), 'create UI string format');
  GdiCheck(GdipSetStringFormatFlags(PathFormat, $1000), 'set UI string flags');
  GdiCheck(GdipSetStringFormatTrimming(PathFormat, 3), 'set UI string trimming');
  SetWindowRgn(WizardForm.Handle, 0, False);
  SetWindowLongPtrW(WizardForm.Handle, -20, (GetWindowLongPtrW(WizardForm.Handle, -20) and not $02000000) or $00080000);
end;

procedure LaunchInstalledWithHandoff;
var
  ResultCode: Integer;
  Args: String;
begin
  if HandoffLaunched then exit;
  HandoffToken := IntToStr(GetCurrentProcessId) + '-' + IntToStr(GetTickCount and $7FFFFFFF);
  HandoffReadyEvent := CreateEventW(0, True, False, 'Local\GluSpot.InstallerHandoff.' + HandoffToken + '.ready');
  HandoffRevealEvent := CreateEventW(0, True, False, 'Local\GluSpot.InstallerHandoff.' + HandoffToken + '.reveal');
  HandoffPaintedEvent := CreateEventW(0, True, False, 'Local\GluSpot.InstallerHandoff.' + HandoffToken + '.painted');
  if (HandoffReadyEvent = 0) or (HandoffRevealEvent = 0) or (HandoffPaintedEvent = 0) then begin
    HandoffFailed := True;
    HandoffLaunched := True;
    Log('Could not prepare installer-to-app synchronization events');
    exit;
  end;
  Args := '--installer-handoff ' + IntToStr(FinalLeft) + ' ' + IntToStr(FinalTop) + ' ' +
    IntToStr(FinalWidth) + ' ' + IntToStr(FinalHeight) + ' --installer-sync ' + HandoffToken;
  RestartAttempted := True;
  if Exec(ExpandConstant('{app}\GluSpot.exe'), Args, ExpandConstant('{app}'),
     SW_SHOWNORMAL, ewNoWait, ResultCode) then begin
    HandoffLaunched := True;
    HandoffStartedTick := GetTickCount;
    Log('Started installed GluSpot with the final physical island rectangle: ' + Args);
  end else begin
    Log('Could not launch installed GluSpot: ' + IntToStr(ResultCode));
    HandoffLaunched := True;
    HandoffFailed := True;
  end;
end;

procedure RenderScene(Time: DWORD);
var
  Elapsed, CompletionElapsed, SinceLoop: DWORD;
  BrandFrame, LowerFrame, Progress, BodyHeight, SloganFrame, OutFrame: Integer;
  Amount: Double;
begin
  if not SceneReady or PaintingScene then exit;
  if (Time - LastCompositionTick < 15) and (LastCompositionTick <> 0) then exit;
  LastCompositionTick := Time;
  PaintingScene := True;
  try
    Elapsed := Time - AnimationStartedTick;
    BrandFrame := ((Elapsed div 1000) * 60 + (Elapsed mod 1000) * 60 div 1000) mod 300;
    SelectedBrand := BrandFrames[BrandFrame];
    BodyHeight := 258;
    CurrentMode := ((Elapsed div 5000) + ModeOffset) mod 4;
    LowerFrame := CurrentMode * 31 + ClampFrame((Elapsed mod 5000) * 60 div 1000, 30);
    SelectedLower := LowerFrames[LowerFrame];
    Progress := 0;
    if CustomPageID = wpInstalling then begin
      if WizardForm.ProgressGauge.Max > 0 then
        Progress := ClampFrame(WizardForm.ProgressGauge.Position * 100 div WizardForm.ProgressGauge.Max, 100);
    end else if CustomPageID = wpFinished then Progress := 100;
    ProgressValue := Progress;
    ProgressOpacity := 1;
    ShadowOpacity := 1;
    ShowBrand := True; ShowLower := True;
    ShowProgress := CustomPageID = wpInstalling; ShowSlogan := False;
    IslandX := 14; IslandY := 12; IslandWidth := 342; IslandHeight := 48;
    UpdateWindowControls(Time);
    if CompletionRunning then begin
      CompletionElapsed := Time - CompletionStartedTick;
      PrimaryButton.Hide; DirectoryButton.Hide; CloseButton.Hide; MinimizeButton.Hide;
      OutFrame := ClampFrame(CompletionElapsed * 60 div 1000, 35);
      SelectedLower := LowerOutFrames[CurrentMode * 36 + OutFrame];
      ProgressOpacity := 1 - Smooth(CompletionElapsed / 600.0);
      ShadowOpacity := ProgressOpacity;
      ShowLower := CompletionElapsed < 600; ShowProgress := CompletionElapsed < 600;
      if CompletionElapsed > 600 then
        BodyHeight := 258 - Round(194 * Smooth((CompletionElapsed - 600) / 600.0));
      if Time >= FinishBrandLoopTick then begin
        SinceLoop := Time - FinishBrandLoopTick;
        Amount := Smooth(SinceLoop / 650.0);
        IslandX := 14 * (1 - Amount); IslandY := 12 * (1 - Amount);
        IslandWidth := 342 + 28 * Amount; IslandHeight := 48 + 16 * Amount;
        ShowBrand := False;
        ShowSlogan := True;
        if SinceLoop < 650 then
          SelectedSlogan := FinishFrames[ClampFrame(SinceLoop * 39 div 650, 39)]
        else begin
          if SinceLoop < 2350 then SloganFrame := 30
          else SloganFrame := 30 - ClampFrame((SinceLoop - 2350) * 30 div 450, 30);
          SelectedSlogan := SloganFrames[SloganFrame];
        end;
        if SinceLoop >= 2800 then begin
          ShowSlogan := False;
          // Quintic S curve: zero endpoint velocity/acceleration; smoothly
          // speeds up after leaving, then slows before the screen-top handoff.
          Amount := (SinceLoop - 2800) / 1600.0;
          if Amount > 1 then Amount := 1;
          Amount := Amount * Amount * Amount * (Amount * (Amount * 6 - 15) + 10);
          SetWindowPos(WizardForm.Handle, 0,
            OriginalWindowRect.Left + Round((FinalLeft - UiX(SceneMargin) - OriginalWindowRect.Left) * Amount),
            OriginalWindowRect.Top + Round((FinalTop - UiY(SceneMargin) - OriginalWindowRect.Top) * Amount), 0, 0, $0015);
          if (Amount >= 1) and not HandoffRevealed then begin
            HandoffRevealed := True;
            HandoffStartedTick := GetTickCount;
            if not HandoffFailed then SetEvent(HandoffRevealEvent);
            Log('Installer arrived at screen top; released app reveal gate');
          end;
          if HandoffRevealed then begin
            if HandoffFailed or (GetTickCount - HandoffStartedTick >= 30000) then begin
              SceneReady := False;
              Log('App did not confirm a first frame; installation remains complete');
              SuppressibleMsgBox('安装已完成，但程序尚未显示。请从安装目录打开 GluSpot。', mbError, MB_OK, IDOK);
              WizardForm.NextButton.OnClick(WizardForm.NextButton);
              exit;
            end;
            if WaitForSingleObject(HandoffPaintedEvent, 0) = 0 then begin
              Log('App confirmed its first painted frame; closing installer');
              SceneReady := False;
              WizardForm.NextButton.OnClick(WizardForm.NextButton);
              exit;
            end;
          end;
        end;
      end;
    end;
    PresentLayeredScene(BodyHeight);
#ifdef VisualDiagnosticControls
    if (CustomPageID = wpSelectDir) and (Elapsed > 750) and ((DiagnosticControlsMask and 1) = 0) then begin
      DiagnosticControlsMask := DiagnosticControlsMask or 1;
      if not CloseButton.Visible or not MinimizeButton.Visible then RaiseException('Moving pointer did not reveal native controls');
      ExportNativeFrame(512, '10-apple-controls-visible');
      MinimizeButton.OnClick(MinimizeButton);
      if not IsIconic(WizardForm.Handle) then RaiseException('Native minimize callback failed');
      Log('Native control acceptance: movement revealed both controls and minimize produced IsIconic=True');
      ShowWindow(WizardForm.Handle, 9);
    end;
    if (CustomPageID = wpSelectDir) and (Elapsed > 1350) and ((DiagnosticControlsMask and 2) = 0) then begin
      DiagnosticControlsMask := DiagnosticControlsMask or 2;
      SendMessageW(WizardForm.Handle, $00A3, 2, 0);
      if IsZoomed(WizardForm.Handle) then RaiseException('Caption double-click allowed maximize');
      if (GetWindowLongPtrW(WizardForm.Handle, -16) and $00050000) <> 0 then RaiseException('Maximize or thick-frame style survived');
      Log('Native control acceptance: caption double-click remained unmaximized, maximize/thick-frame styles absent');
    end;
    if (CustomPageID = wpSelectDir) and (Elapsed > 4100) and ((DiagnosticControlsMask and 4) = 0) then begin
      DiagnosticControlsMask := DiagnosticControlsMask or 4;
      if CloseButton.Visible or MinimizeButton.Visible or CloseButton.TabStop or MinimizeButton.TabStop then RaiseException('Idle controls retained invisible input targets');
      ExportNativeFrame(1024, '11-apple-controls-idle-hidden');
      Log('Native control acceptance: idle removed both visual controls and TabStop hit targets');
#ifdef VisualDiagnosticClose
      Log('Native control acceptance: invoking actual close button callback');
      CloseButton.OnClick(CloseButton);
#endif
    end;
#endif
#ifdef VisualDiagnosticDir
    if GetTickCount - DiagnosticLastFrameTick >= 30 then begin
      DiagnosticLastFrameTick := GetTickCount;
      ExportLayerFrame('frame-' + IntToStr(DiagnosticFrameIndex), False);
      DiagnosticFrameIndex := DiagnosticFrameIndex + 1;
    end;
    if CustomPageID = wpSelectDir then begin
#ifdef VisualDiagnosticControls
      if Elapsed > 4400 then begin
#else
      if Elapsed > 1200 then begin
#endif
        ExportNativeFrame(1, '01-before-install');
        WizardForm.NextButton.OnClick(WizardForm.NextButton);
      end;
    end;
    if CustomPageID = wpInstalling then ExportNativeFrame(2, '02-real-install-progress');
    if CompletionRunning then begin
      if (CompletionElapsed > 250) and (CompletionElapsed < 600) then ExportNativeFrame(4, '03-lower-exit');
      if (CompletionElapsed > 850) and (CompletionElapsed < 1200) then ExportNativeFrame(8, '04-body-shrink');
      if (CompletionElapsed > 1250) and (Time < FinishBrandLoopTick) then ExportNativeFrame(16, '05-final-brand-loop');
      if (Time >= FinishBrandLoopTick) and (SinceLoop > 250) and (SinceLoop < 650) then ExportNativeFrame(32, '06-black-expands');
      if (Time >= FinishBrandLoopTick) and (SinceLoop > 1200) and (SinceLoop < 2350) then ExportNativeFrame(64, '07-source-han-slogan');
      if (Time >= FinishBrandLoopTick) and (SinceLoop > 3000) and (SinceLoop < 4000) then ExportNativeFrame(128, '08-eased-travel');
      if HandoffLaunched then ExportNativeFrame(256, '09-final-island-handoff');
    end;
#endif
  finally
    PaintingScene := False;
  end;
end;

procedure RotatePalette(Wnd: HWND; Msg: UINT; TimerID: UINT_PTR; Time: DWORD);
begin
  if not TimerTicked then begin
    TimerTicked := True;
    Log('First compact timer tick: ' + IntToStr(Time) + ', gettick=' + IntToStr(GetTickCount));
  end;
  if CustomPageID = wpWelcome then begin
    WizardForm.NextButton.OnClick(WizardForm.NextButton);
    exit;
  end;
  RenderScene(Time);
end;

procedure StartOrFinish(Sender: TObject);
begin
  if CustomPageID = wpSelectDir then WizardForm.NextButton.OnClick(WizardForm.NextButton);
end;

function DefaultInstallDirectory(SelectedFolder: String): String;
begin
  SelectedFolder := RemoveBackslashUnlessRoot(ExpandFileName(SelectedFolder));
  // Browsing selects a parent. Keep an explicit GluSpot folder or recognized
  // installation intact; otherwise offer a dedicated child, never the parent.
  if IsRecognizedInstallation(SelectedFolder) or
     (CompareText(ExtractFileName(SelectedFolder), 'GluSpot') = 0) then
    Result := SelectedFolder
  else Result := AddBackslash(SelectedFolder) + 'GluSpot';
end;

function IsAbsoluteInstallDirectory(Target: String): Boolean;
var
  Server, Share: String;
  Separator: Integer;
begin
  Result := False;
  Target := Trim(Target);
  StringChangeEx(Target, '/', '\', True);
  // ExpandFileName also accepts drive-relative C:folder. Require an actual
  // rooted drive path, or an ordinary UNC server/share, before resolving it.
  if Length(Target) < 3 then exit;
  if (Pos(Uppercase(Copy(Target, 1, 1)), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') > 0) and
     (Copy(Target, 2, 2) = ':\') then begin Result := True; exit; end;
  if Copy(Target, 1, 2) <> '\\' then exit;
  Delete(Target, 1, 2);
  Separator := Pos('\', Target);
  if Separator < 2 then exit;
  Server := Copy(Target, 1, Separator - 1);
  Delete(Target, 1, Separator);
  Separator := Pos('\', Target);
  if Separator = 0 then Share := Target
  else Share := Copy(Target, 1, Separator - 1);
  // Device/extended prefixes and missing server/share names are not folders.
  Result := (Trim(Server) <> '') and (Server <> '.') and (Server <> '?') and
    (Trim(Share) <> '') and (Share <> '.') and (Share <> '..');
end;

function DirectoryValidationError(Target: String): String;
begin
  Result := '';
  if not IsAbsoluteInstallDirectory(Target) or
     (Pos('*', Target) > 0) or (Pos('?', Target) > 0) then begin
    Result := '请输入完整的绝对路径，或点击“浏览父文件夹”。'; exit;
  end;
  Result := RegisteredTargetError(Target);
  if (Result = '') and not ValidInstallTarget(Target) then
    Result := '请选择 GluSpot 专用空目录或已有的 GluSpot 目录；不能使用系统目录、其他文件所在目录或重定向目录。';
end;

procedure BrowseDirectoryParent(Sender: TObject);
var
  Path: String;
begin
  if DirectoryRegistered <> '' then exit;
  Path := Trim(DirectoryPathEdit.Text);
  if not IsAbsoluteInstallDirectory(Path) then Path := WizardDirValue;
  Path := ExtractFileDir(RemoveBackslashUnlessRoot(ExpandFileName(Path)));
  if BrowseForFolder('选择父文件夹；将自动使用其中的 GluSpot 子目录', Path, True) then begin
    DirectoryPathEdit.Text := DefaultInstallDirectory(Path);
    DirectoryStatus.Font.Color := $00656B63;
    DirectoryStatus.Caption := '已为所选父文件夹使用 GluSpot 专用子目录。也可直接编辑上方完整路径。';
    DirectoryManager.ActiveControl := DirectoryPathEdit;
    DirectoryPathEdit.SelectAll;
  end;
end;

function ApplyDirectory: Boolean;
var
  Target, Error: String;
begin
  Result := True;
  if DirectoryRegistered <> '' then exit;
  Target := Trim(DirectoryPathEdit.Text);
  Error := DirectoryValidationError(Target);
  if Error <> '' then begin
    DirectoryStatus.Font.Color := $003333C6;
    DirectoryStatus.Caption := Error;
    DirectoryManager.ActiveControl := DirectoryPathEdit;
    Result := False;
    exit;
  end;
  Target := RemoveBackslashUnlessRoot(ExpandFileName(Target));
  WizardForm.DirEdit.Text := Target;
  DirectoryButton.Hint := Target + #13#10 + '点击查看完整路径或更改安装位置';
  ApprovedTarget := '';
end;

procedure SelectDirectory(Sender: TObject);
var
  LabelText: TNewStaticText;
  BrowseButton, ApplyButton, CancelButton: TNewButton;
begin
  if (CustomPageID <> wpSelectDir) or DirectoryManagerOpen then exit;
  DirectoryRegistered := RegisteredInstallDirectory;
  DirectoryManager := CreateCustomForm(UiX(520), UiY(230), False, True);
  DirectoryManagerOpen := True;
  try
    DirectoryManager.Caption := 'GluSpot 安装位置';
    DirectoryManager.Font.Name := WizardForm.Font.Name;
    LabelText := TNewStaticText.Create(DirectoryManager);
    LabelText.Parent := DirectoryManager;
    LabelText.SetBounds(UiX(18), UiY(16), UiX(484), UiY(23));
    LabelText.Caption := '完整安装路径（可选择并复制）';
    DirectoryPathEdit := TNewEdit.Create(DirectoryManager);
    DirectoryPathEdit.Parent := DirectoryManager;
    DirectoryPathEdit.SetBounds(UiX(18), UiY(43), UiX(484), UiY(27));
    DirectoryPathEdit.Text := WizardDirValue;
    DirectoryPathEdit.ReadOnly := DirectoryRegistered <> '';
    DirectoryStatus := TNewStaticText.Create(DirectoryManager);
    DirectoryStatus.Parent := DirectoryManager;
    DirectoryStatus.AutoSize := False;
    DirectoryStatus.WordWrap := True;
    DirectoryStatus.SetBounds(UiX(18), UiY(83), UiX(484), UiY(72));
    DirectoryStatus.Font.Color := $00656B63;
    if DirectoryRegistered <> '' then
      DirectoryStatus.Caption := '检测到已有安装。升级保留原目录，以完整保留账号、设置和缓存。此处可查看并复制位置。若需更换目录，请先使用原版本的卸载程序。'
    else
      DirectoryStatus.Caption := '选择父文件夹时，会自动追加 GluSpot 专用子目录，避免将程序装到父目录。也可以直接编辑完整路径；已有安装不会重复追加。';
    BrowseButton := TNewButton.Create(DirectoryManager);
    BrowseButton.Parent := DirectoryManager;
    BrowseButton.SetBounds(UiX(18), UiY(180), UiX(156), UiY(30));
    BrowseButton.Caption := '浏览父文件夹…';
    BrowseButton.OnClick := @BrowseDirectoryParent;
    BrowseButton.Visible := DirectoryRegistered = '';
    ApplyButton := TNewButton.Create(DirectoryManager);
    ApplyButton.Parent := DirectoryManager;
    ApplyButton.SetBounds(UiX(288), UiY(180), UiX(103), UiY(30));
    ApplyButton.Caption := '使用此目录';
    if DirectoryRegistered <> '' then ApplyButton.Caption := '知道了';
    ApplyButton.Default := True;
    ApplyButton.ModalResult := mrOk;
    CancelButton := TNewButton.Create(DirectoryManager);
    CancelButton.Parent := DirectoryManager;
    CancelButton.SetBounds(UiX(399), UiY(180), UiX(103), UiY(30));
    CancelButton.Caption := '取消';
    CancelButton.Cancel := True;
    CancelButton.ModalResult := mrCancel;
    DirectoryManager.ActiveControl := DirectoryPathEdit;
    DirectoryManager.FlipAndCenterIfNeeded(True, WizardForm, False);
    while DirectoryManager.ShowModal = mrOk do begin
      if ApplyDirectory then break;
    end;
  finally
    DirectoryManagerOpen := False;
    DirectoryManager.Free;
    DirectoryManager := nil;
    DirectoryPathEdit := nil;
    DirectoryStatus := nil;
    LastCompositionTick := 0;
    RenderScene(GetTickCount);
  end;
end;

procedure CloseInstaller(Sender: TObject);
begin
  // MainForm.Close checks the native cancel button's CanFocus before it
  // invokes confirmation/rollback. Its offscreen HWND stays enabled/visible.
  WizardForm.Close;
end;

procedure MinimizeInstaller(Sender: TObject);
begin
  ShowWindow(WizardForm.Handle, 6);
end;

function PointOverButton(Button: TBitmapButton; Point: TPointerPoint): Boolean;
begin
  Result := Button.Visible and (Point.X >= Button.Left) and (Point.Y >= Button.Top) and
    (Point.X < Button.Left + Button.Width) and (Point.Y < Button.Top + Button.Height);
end;

function InstallerDragHit(Point: TPointerPoint): Boolean;
begin
  Result := SceneReady and not CompletionRunning and not DirectoryManagerOpen and
    IsWindowEnabled(WizardForm.Handle) and
    (Point.X >= UiX(SceneMargin)) and (Point.Y >= UiY(SceneMargin)) and
    (Point.X < UiX(SceneMargin + 370)) and (Point.Y < UiY(SceneMargin + LastBodyHeight));
  if not Result then exit;
  Result := not PointOverButton(PrimaryButton, Point) and
    not PointOverButton(DirectoryButton, Point) and
    not PointOverButton(CloseButton, Point) and
    not PointOverButton(MinimizeButton, Point);
end;

function InstallerWindowSubclass(Window: HWND; Msg: UINT; WParam: NativeUInt;
  LParam: NativeInt; SubclassID: UINT_PTR; RefData: NativeUInt): NativeInt;
var
  Point: TPointerPoint;
begin
  if Msg = $0084 then begin // WM_NCHITTEST is resolved before button down.
    Point.X := LParam and $FFFF;
    Point.Y := (LParam shr 16) and $FFFF;
    if Point.X >= 32768 then Point.X := Point.X - 65536;
    if Point.Y >= 32768 then Point.Y := Point.Y - 65536;
    if ScreenToClient(Window, Point) and InstallerDragHit(Point) then begin
      Result := 2; exit; // HTCAPTION invokes Windows' standard press/drag loop.
    end;
  end;
  Result := DefSubclassProc(Window, Msg, WParam, LParam);
end;

function MakeButton(Left, Top, Width, Height: Integer; Caption: String; Click: TNotifyEvent): TBitmapButton;
begin
  Result := TBitmapButton.Create(WizardForm);
  Result.Parent := WizardForm;
  Result.SetBounds(UiX(Left + SceneMargin), UiY(Top + SceneMargin), UiX(Width), UiY(Height));
  Result.Caption := Caption;
  Result.Cursor := crHand;
  Result.TabStop := True;
  Result.OnClick := Click;
end;

function LoadPNG(Name: String): NativeUInt;
begin
  GdiCheck(GdipCreateBitmapFromFile(ExpandConstant('{tmp}\') + Name + '.png', Result), 'load cached PNG ' + Name);
end;

function Number3(Index: Integer): String;
begin
  Result := IntToStr(Index);
  while Length(Result) < 3 do Result := '0' + Result;
end;

function Number2(Index: Integer): String;
begin
  Result := IntToStr(Index);
  while Length(Result) < 2 do Result := '0' + Result;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpWelcome) or (PageID = wpReady);
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Elapsed: DWORD;
  Monitor: TMonitorData;
begin
  CustomPageID := CurPageID;
  Log('Compact page: ' + IntToStr(CurPageID));
  if not SceneReady then exit;
  WizardForm.OuterNotebook.Hide;
  WizardForm.NextButton.Hide;
  WizardForm.BackButton.Hide;
  // Inno MainForm.Close requires CancelButton.CanFocus. Keep the engine's
  // button offscreen and out of tab order; hiding it silently disables Close.
  WizardForm.CancelButton.SetBounds(-100, -100, 1, 1);
  WizardForm.CancelButton.TabStop := False;
  WizardForm.CancelButton.Visible := CurPageID <> wpFinished;
  PrimaryButton.Visible := CurPageID = wpSelectDir;
  DirectoryButton.Visible := CurPageID = wpSelectDir;
  // Cancellation keeps the native engine authority during extraction.
  CloseButton.Enabled := WizardForm.CancelButton.Enabled and (CurPageID <> wpFinished);
  if CurPageID = wpFinished then begin
    CompletionRunning := True;
    CompletionStartedTick := GetTickCount;
    Elapsed := CompletionStartedTick - AnimationStartedTick;
    FinishBrandLoopTick := AnimationStartedTick + ((Elapsed + 4999) div 5000) * 5000;
    if FinishBrandLoopTick < CompletionStartedTick + 1200 then
      FinishBrandLoopTick := FinishBrandLoopTick + 5000;
    GetWindowRect(WizardForm.Handle, OriginalWindowRect);
    Monitor.Size := 40;
    if GetMonitorInfoW(MonitorFromWindow(WizardForm.Handle, 2), Monitor) then begin
      FinalWidth := UiX(370);
      FinalHeight := UiY(64);
      FinalLeft := Monitor.WorkRect.Left + (Monitor.WorkRect.Right - Monitor.WorkRect.Left - FinalWidth) div 2;
      FinalTop := Monitor.WorkRect.Top + UiY(12);
    end else begin
      FinalWidth := UiX(370); FinalHeight := UiY(64);
      FinalLeft := OriginalWindowRect.Left; FinalTop := UiY(12);
    end;
    // Qt/runtime initialization overlaps the existing completion storyboard.
    // The app waits on reveal, so it cannot cover this animation early.
    LaunchInstalledWithHandoff;
  end;
  RenderScene(GetTickCount);
end;

procedure InitializeWizard;
var
  Registered: String;
  Index, Mode, Frame: Integer;
begin
  Registered := RegisteredInstallDirectory;
  if Registered <> '' then begin
    if WizardSilent then RequestedSilentTargetError := RegisteredTargetError(WizardDirValue);
    WizardForm.DirEdit.Text := Registered;
    WizardForm.DirEdit.Enabled := False;
  end;
  // Noninteractive acceptance and updates neither allocate visual cache nor
  // launch fresh apps. Running-target updates still restart once on success.
  if WizardSilent then exit;
  WizardForm.BorderStyle := bsNone;
  WizardForm.BorderIcons := [];
  WizardForm.ClientWidth := UiX(370 + SceneMargin * 2);
  WizardForm.ClientHeight := UiY(258 + SceneMargin * 2);
  WizardForm.Position := poScreenCenter;
  WizardForm.Color := clWhite;
  InitializeLayeredScene;
  if SceneFontName <> '' then WizardForm.Font.Name := SceneFontName;
  // No maximize box, thick frame or nonclient double-click maximize route.
  SetWindowLongPtrW(WizardForm.Handle, -16, GetWindowLongPtrW(WizardForm.Handle, -16) and not $00050000);
  WizardForm.OuterNotebook.Hide;
  WizardForm.Bevel.Hide;
  WizardForm.MainPanel.Hide;
  WizardForm.NextButton.Hide;
  WizardForm.BackButton.Hide;
  WizardForm.CancelButton.Hide;
  ExtractTemporaryFiles('{tmp}\compact-*.png');
  for Index := 0 to 299 do BrandFrames[Index] := LoadPNG('compact-brand-' + Number3(Index));
  for Mode := 0 to 3 do begin
    for Frame := 0 to 30 do
      LowerFrames[Mode * 31 + Frame] := LoadPNG('compact-lower-' + IntToStr(Mode) + '-' + Number2(Frame));
    for Frame := 0 to 35 do
      LowerOutFrames[Mode * 36 + Frame] := LoadPNG('compact-lower-out-' + IntToStr(Mode) + '-' + Number2(Frame));
  end;
  for Index := 0 to 30 do SloganFrames[Index] := LoadPNG('compact-slogan-' + Number2(Index));
  for Index := 0 to 39 do FinishFrames[Index] := LoadPNG('compact-finish-' + Number2(Index));
  for Index := 0 to 30 do begin
    CloseFrames[Index] := LoadPNG('compact-control-close-' + Number2(Index));
    MinimizeFrames[Index] := LoadPNG('compact-control-minimize-' + Number2(Index));
    ControlCapsuleFrames[Index] := LoadPNG('compact-control-capsule-' + Number2(Index));
  end;
  Log('Compact artwork cache ready.');
  PrimaryButton := MakeButton(262, 214, 92, 30, '安装 GluSpot', @StartOrFinish);
  DirectoryButton := MakeButton(16, 214, 236, 30, '更改安装位置', @SelectDirectory);
  DirectoryButton.Enabled := True;
  DirectoryRegistered := Registered;
  DirectoryButton.Hint := WizardDirValue;
  DirectoryButton.ShowHint := True;
  CloseButton := MakeButton(26, 26, 20, 20, '关闭安装器', @CloseInstaller);
  CloseButton.Hide;
  MinimizeButton := MakeButton(54, 26, 20, 20, '最小化安装器', @MinimizeInstaller);
  MinimizeButton.Hide;
  SetWindowRgn(CloseButton.Handle, CreateEllipticRgn(0,0,UiX(20),UiY(20)), True);
  SetWindowRgn(MinimizeButton.Handle, CreateEllipticRgn(0,0,UiX(20),UiY(20)), True);
  Log('Compact native controls ready.');
  Log('Builtin page ids: welcome=' + IntToStr(wpWelcome) + ', dir=' + IntToStr(wpSelectDir) + ', finished=' + IntToStr(wpFinished));
  AnimationStartedTick := GetTickCount;
  ModeOffset := AnimationStartedTick mod 4;
  LastBrandFrame := -1; LastLowerFrame := -1;
  LastControlFrame := -1; LastControlTick := AnimationStartedTick;
  SceneReady := True;
  InstallerSubclassCallback := CreateCallback(@InstallerWindowSubclass);
  InstallerSubclassInstalled := SetWindowSubclass(WizardForm.Handle, InstallerSubclassCallback, 7412, 0);
  if not InstallerSubclassInstalled then RaiseException('Installer native drag handler could not be attached');
  RenderScene(AnimationStartedTick);
  Log('Compact first native composition ready.');
  TimerResolutionHeld := timeBeginPeriod(1) = 0;
  PaletteTimer := SetTimer(0, 0, 8, CreateCallback(@RotatePalette));
  Log('Compact scene timer: ' + IntToStr(PaletteTimer));
#ifdef VisualDiagnosticDir
  WizardForm.Show;
#endif
end;

#ifdef VisualDiagnosticClose
procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  Cancel := True; Confirm := False;
end;
#endif

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then begin
    InstallCompleted := True;
    if BackupReady then RemoveOwnedTree(BackupPath, ExpandConstant('{app}'));
  end;
end;

procedure DeinitializeSetup;
var
  Index, ResultCode: Integer;
begin
  SceneReady := False;
  if HandoffReadyEvent <> 0 then CloseHandle(HandoffReadyEvent);
  if HandoffRevealEvent <> 0 then CloseHandle(HandoffRevealEvent);
  if HandoffPaintedEvent <> 0 then CloseHandle(HandoffPaintedEvent);
  if InstallerSubclassInstalled then RemoveWindowSubclass(WizardForm.Handle, InstallerSubclassCallback, 7412);
  if PaletteTimer <> 0 then KillTimer(0, PaletteTimer);
  if TimerResolutionHeld then timeEndPeriod(1);
  for Index := 0 to 299 do if BrandFrames[Index] <> 0 then GdipDisposeImage(BrandFrames[Index]);
  for Index := 0 to 123 do if LowerFrames[Index] <> 0 then GdipDisposeImage(LowerFrames[Index]);
  for Index := 0 to 143 do if LowerOutFrames[Index] <> 0 then GdipDisposeImage(LowerOutFrames[Index]);
  for Index := 0 to 30 do if SloganFrames[Index] <> 0 then GdipDisposeImage(SloganFrames[Index]);
  for Index := 0 to 39 do if FinishFrames[Index] <> 0 then GdipDisposeImage(FinishFrames[Index]);
  for Index := 0 to 30 do begin
    if CloseFrames[Index] <> 0 then GdipDisposeImage(CloseFrames[Index]);
    if MinimizeFrames[Index] <> 0 then GdipDisposeImage(MinimizeFrames[Index]);
    if ControlCapsuleFrames[Index] <> 0 then GdipDisposeImage(ControlCapsuleFrames[Index]);
  end;
  if PathFormat <> 0 then GdipDeleteStringFormat(PathFormat);
  if DirectoryFont <> 0 then GdipDeleteFont(DirectoryFont);
  if PrimaryFont <> 0 then GdipDeleteFont(PrimaryFont);
  if FontFamily <> 0 then GdipDeleteFontFamily(FontFamily);
  if LayerGraphics <> 0 then GdipDeleteGraphics(LayerGraphics);
  if LayerImage <> 0 then GdipDisposeImage(LayerImage);
  if LayerDC <> 0 then begin
    SelectObject(LayerDC, OldLayerDIB);
    DeleteDC(LayerDC);
  end;
  if LayerDIB <> 0 then DeleteObject(LayerDIB);
  if GdiToken <> 0 then GdiplusShutdown(GdiToken);
  if not InstallCompleted then RestoreBackup;
  // Silent upgrades restart only the exact installation that was running.
  // The graphical installer launched once with the final island rectangle.
  if StoppedRunningTarget and not RestartAttempted and
     FileExists(ExpandConstant('{app}\GluSpot.exe')) then begin
    RestartAttempted := True;
    if not Exec(ExpandConstant('{app}\GluSpot.exe'), '', ExpandConstant('{app}'),
      SW_SHOWNORMAL, ewNoWait, ResultCode) then
      Log('Could not restart the replaced GluSpot: ' + IntToStr(ResultCode));
  end;
end;

function InitializeUninstall: Boolean;
var
  Target, Recorded: String;
  Handle: THandle;
begin
  Target := RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}')));
  Recorded := GetIniString('GluSpot', 'InstallPath', '', Target + '\gluspot-install.ini');
  Result := (Length(Target) >= 4) and (CompareText(Target, Recorded) = 0) and not HasRedirectedParent(Target);
  if not Result then SuppressibleMsgBox('安装目录身份无法验证。为保护目录中的其他文件，卸载已停止。', mbError, MB_OK, IDOK);
  if not Result or not FileExists(Target + '\GluSpot.exe') then exit;
  Handle := CreateFileW(Target + '\GluSpot.exe', $40000000, 7, 0, 3, 0, 0);
  if Handle = THandle(-1) then begin
    Result := False;
    SuppressibleMsgBox('请先正常退出 GluSpot，再卸载程序。', mbError, MB_OK, IDOK);
    exit;
  end;
  CloseHandle(Handle);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then begin
    if not RemoveOwnedTree(ExpandConstant('{app}\GluSpot'), ExpandConstant('{app}')) then
      Log('Retained redirected or unavailable GluSpot state directory; no outside path was deleted.');
    RemoveOwnedTree(ExpandConstant('{app}\_internal'), ExpandConstant('{app}'));
  end;
end;

