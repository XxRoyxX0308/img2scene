# Image -> Blender scene pipeline (v2: FLUX.2 klein object images + TRELLIS.2 + Blender).
# scene.json (objects, bboxes, depths, prompts) is the single source of truth.
#
#   .\pipeline\run_all.ps1 generate   # steps 1-2 + contact sheets, then STOP for review
#   ... look at work\objects\<id>\sheet.png, write work\selection.json  {"<id>": {"pick": 2, "why": "..."}}
#   .\pipeline\run_all.ps1 build      # steps 3-5: matte, TRELLIS, Blender
#
# Finished candidates / GLBs are skipped. Delete the file to redo one object.
param([Parameter(Mandatory)][ValidateSet('generate','build')][string]$Stage)
$ErrorActionPreference = 'Stop'
$proj    = Split-Path -Parent $PSScriptRoot
$projWsl = '/mnt/' + $proj.Substring(0,1).ToLower() + ($proj.Substring(2) -replace '\\','/')
$blender = 'D:\Blender Foundation\Blender 5.2\blender.exe'
$conda   = 'source ~/miniconda3/etc/profile.d/conda.sh'

if ($Stage -eq 'generate') {
    Write-Host '== 1. reference crops =='
    wsl -e bash -lc "$conda; conda activate trellis2; python $projWsl/pipeline/01_make_refs.py $projWsl"
    Write-Host '== 2. FLUX.2 klein candidates (starts ComfyUI, stops it afterwards to free VRAM) =='
    wsl -e bash -lc "$conda; conda activate comfyui; cd ~/ComfyUI; (python main.py --listen 127.0.0.1 --port 8188 > ~/comfyui.log 2>&1 &) ; until curl -s -m 2 http://127.0.0.1:8188/system_stats > /dev/null; do sleep 3; done; python3 $projWsl/pipeline/02_generate.py $projWsl; pkill -f '[m]ain.py --listen'"
    wsl -e bash -lc "$conda; conda activate trellis2; python $projWsl/pipeline/03_select_matte.py $projWsl sheet"
    Write-Host "review work\objects\*\sheet.png and write work\selection.json, then run: .\pipeline\run_all.ps1 build"
} else {
    Write-Host '== 3. background removal of the selected candidates =='
    wsl -e bash -lc "$conda; conda activate trellis2; python $projWsl/pipeline/03_select_matte.py $projWsl matte"
    Write-Host '== 4. TRELLIS.2 image -> 3D =='
    wsl -e bash -lc "bash $projWsl/pipeline/trellis_loop.sh $projWsl 8"
    Write-Host '== 4b. inspection renders of every model (work\inspect) =='
    & $blender -b -P "$proj\pipeline\06_inspect_models.py" -- $proj
    Write-Host '== 5. build Blender scene =='
    & $blender -b -P "$proj\pipeline\05_build_scene.py" -- $proj
    Write-Host "done: $proj\scene.blend, renders in $proj\work\renders"
}
