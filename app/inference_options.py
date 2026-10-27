"""CPU thread settings shared by detector and facial models."""
import os
import onnxruntime as ort

def session_options(num_threads):
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, int(num_threads))
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    spin = "1" if os.getenv("ORT_ALLOW_SPINNING", "0").lower() in ("1", "true", "yes") else "0"
    options.add_session_config_entry("session.intra_op.allow_spinning", spin)
    options.add_session_config_entry("session.inter_op.allow_spinning", spin)
    return options
