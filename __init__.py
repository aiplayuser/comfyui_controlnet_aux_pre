import sys, os
from .utils import here, create_node_input_types
from pathlib import Path
import threading
import traceback
import warnings
import importlib
from .log import log, blue_text, cyan_text, get_summary, get_label
from .hint_image_enchance import NODE_CLASS_MAPPINGS as HIE_NODE_CLASS_MAPPINGS
from .hint_image_enchance import NODE_DISPLAY_NAME_MAPPINGS as HIE_NODE_DISPLAY_NAME_MAPPINGS
#Ref: https://github.com/comfyanonymous/ComfyUI/blob/76d53c4622fc06372975ed2a43ad345935b8a551/nodes.py#L17
sys.path.insert(0, str(Path(here, "src").resolve()))
for pkg_name in ["controlnet_aux", "custom_mmpkg"]:
    sys.path.append(str(Path(here, "src", pkg_name).resolve()))

#Enable CPU fallback for ops not being supported by MPS like upsample_bicubic2d.out
#https://github.com/pytorch/pytorch/issues/77764
#https://github.com/Fannovel16/comfyui_controlnet_aux/issues/2#issuecomment-1763579485
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = '1' 


def load_nodes():
    shorted_errors = []
    full_error_messages = []
    node_class_mappings = {}
    node_display_name_mappings = {}

    for filename in (here / "node_wrappers").iterdir():
        module_name = filename.stem
        if module_name.startswith('.'): continue #Skip hidden files created by the OS (e.g. [.DS_Store](https://en.wikipedia.org/wiki/.DS_Store))
        try:
            module = importlib.import_module(
                f".node_wrappers.{module_name}", package=__package__
            )
            node_class_mappings.update(getattr(module, "NODE_CLASS_MAPPINGS"))
            if hasattr(module, "NODE_DISPLAY_NAME_MAPPINGS"):
                node_display_name_mappings.update(getattr(module, "NODE_DISPLAY_NAME_MAPPINGS"))

            log.debug(f"Imported {module_name} nodes")

        except AttributeError:
            pass  # wip nodes
        except Exception:
            error_message = traceback.format_exc()
            full_error_messages.append(error_message)
            error_message = error_message.splitlines()[-1]
            shorted_errors.append(
                f"Failed to import module {module_name} because {error_message}"
            )
    
    if len(shorted_errors) > 0:
        full_err_log = '\n\n'.join(full_error_messages)
        print(f"\n\nFull error log from comfyui_controlnet_aux: \n{full_err_log}\n\n")
        log.info(
            f"Some nodes failed to load:\n\t"
            + "\n\t".join(shorted_errors)
            + "\n\n"
            + "Check that you properly installed the dependencies.\n"
            + "If you think this is a bug, please report it on the github page (https://github.com/Fannovel16/comfyui_controlnet_aux/issues)"
        )
    return node_class_mappings, node_display_name_mappings

AUX_NODE_MAPPINGS, AUX_DISPLAY_NAME_MAPPINGS = load_nodes()

AIO_NOT_SUPPORTED = ["InpaintPreprocessor"]
#For nodes not mapping image to image

def preprocessor_options():
    auxs = list(AUX_NODE_MAPPINGS.keys())
    auxs.insert(0, "none")
    for name in AIO_NOT_SUPPORTED:
        if name in auxs:
            auxs.remove(name)
    return auxs


PREPROCESSOR_OPTIONS = preprocessor_options()

class AIO_Preprocessor:
    @classmethod
    def INPUT_TYPES(s):
        return create_node_input_types(preprocessor=(PREPROCESSOR_OPTIONS, {"default": "none"}))

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "execute"

    CATEGORY = "ControlNet Preprocessors"

    def execute(self, preprocessor, image, resolution=512):
        if preprocessor == "none":
            return (image, )
        else:
            aux_class = AUX_NODE_MAPPINGS[preprocessor]
            input_types = aux_class.INPUT_TYPES()
            input_types = {
                **input_types["required"],
                **(input_types["optional"] if "optional" in input_types else {})
            }
            params = {}
            for name, input_type in input_types.items():
                if name == "image":
                    params[name] = image
                    continue

                if name == "resolution":
                    params[name] = resolution
                    continue

                if len(input_type) == 2 and ("default" in input_type[1]):
                    params[name] = input_type[1]["default"]
                    continue

                default_values = { "INT": 0, "FLOAT": 0.0 }
                if input_type[0] in default_values:
                    params[name] = default_values[input_type[0]]

            return getattr(aux_class(), aux_class.FUNCTION)(**params)

##########################################################################################################################
WEB_DIRECTORY = "./web"
from server import PromptServer
from aiohttp import web
import folder_paths, comfy.controlnet
@PromptServer.instance.routes.get("/Preprocessor")
async def getStylesList(request):
    cnmodelname = request.rel_url.query["name"]
    return web.json_response([{"name":i} for i in preprocessor_options()])

class ControlNetPreprocessorSelector:
    @classmethod
    def INPUT_TYPES(s):
        return { "required": { "cn": ( folder_paths.get_filename_list("controlnet"), ), 
                               "image": ("IMAGE",), },
                 "hidden": { "prompt": "PROMPT", "my_unique_id": "UNIQUE_ID" }, 
                 "optional": { "resolution": ("INT", {"default": 512, "min": 64, "max": 4096, "step": 64 } ) }    }

    RETURN_TYPES = ("CONTROL_NET","IMAGE")
    FUNCTION = "get_preprocessor"
    CATEGORY = "ControlNet Preprocessors"
    OUTPUT_NODE = True

    def get_preprocessor(self, cn, image, resolution=512, prompt=None, my_unique_id=None): 
        controlnet = comfy.controlnet.load_controlnet( folder_paths.get_full_path("controlnet", cn) )
        pre = prompt[my_unique_id]["inputs"]['select_styles']
        print(prompt)
        if pre == "none": return (controlnet, image )
        else:
            aux_class = AUX_NODE_MAPPINGS[pre]
            input_types = aux_class.INPUT_TYPES()
            input_types = {
                **input_types["required"],
                **(input_types["optional"] if "optional" in input_types else {})
            }
            params = {}
            for name, input_type in input_types.items():
                if name == "image":
                    params[name] = image
                    continue

                if name == "resolution":
                    params[name] = resolution
                    continue

                if len(input_type) == 2 and ("default" in input_type[1]):
                    params[name] = input_type[1]["default"]
                    continue

                default_values = { "INT": 0, "FLOAT": 0.0 }
                if input_type[0] in default_values: params[name] = default_values[input_type[0]]
                
            predict = getattr(aux_class(), aux_class.FUNCTION)(**params)

            if isinstance(predict, dict): return (controlnet,) + predict["result"] 
            else: return (controlnet,) + predict
##########################################################################################################################

NODE_CLASS_MAPPINGS = {
    **AUX_NODE_MAPPINGS,
    "AIO_Preprocessor": AIO_Preprocessor,
    "ControlNetPreprocessorSelector": ControlNetPreprocessorSelector,
    **HIE_NODE_CLASS_MAPPINGS,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    **AUX_DISPLAY_NAME_MAPPINGS,
    "AIO_Preprocessor": "AIO Aux Preprocessor",
    "ControlNetPreprocessorSelector": "Preprocessor Selector",
    **HIE_NODE_DISPLAY_NAME_MAPPINGS,
}
