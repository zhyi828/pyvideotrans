import multiprocessing
import os
import re
import time
from pathlib import Path

import torch
import zhconv
from faster_whisper import WhisperModel

from videotrans.util.tools import ms_to_time_string


def run(raws, err,detect, *, model_name, is_cuda, detect_language, audio_file, maxlen, flag, join_word_flag,
        q: multiprocessing.Queue, ROOT_DIR, TEMP_DIR, settings, defaulelang):
    os.chdir(ROOT_DIR)
    down_root = ROOT_DIR + "/models"
    def write_log(jsondata):
        try:
            q.put_nowait(jsondata)
        except:
            pass

    try:
        # 不存在 / ，是普通本地已有模型，直接本地加载，否则在线下载
        local_res = True if model_name.find('/') == -1 else False
        if not local_res:
            if not os.path.isdir(down_root + '/models--' + model_name.replace('/', '--')):
                msg = '下载模型中，用时可能较久' if defaulelang == 'zh' else 'Download model from huggingface'
            else:
                msg = '加载或下载模型中，用时可能较久' if defaulelang == 'zh' else 'Load model from local or download model from huggingface'
            write_log({"text": msg, "type": "logs"})
        if model_name.startswith('distil-'):
            com_type = "default"
        elif is_cuda:
            com_type = settings['cuda_com_type']
        else:
            com_type = settings['cuda_com_type']
        try:
            model = WhisperModel(
                model_name,
                device="cuda" if is_cuda else "cpu",
                compute_type=com_type,
                download_root=down_root,
                num_workers=settings['whisper_worker'],
                cpu_threads=os.cpu_count() if int(settings['whisper_threads']) < 1 else int(
                    settings['whisper_threads']),
                local_files_only=local_res

            )
        except Exception as e:
            if re.match(r'backend do not support', str(e), re.I):
                # 如果所选数据类型不支持，则使用默认
                model = WhisperModel(
                    model_name,
                    device="cuda" if is_cuda else "cpu",
                    compute_type="default",
                    download_root=down_root,
                    num_workers=settings['whisper_worker'],
                    cpu_threads=os.cpu_count() if int(settings['whisper_threads']) < 1 else int(
                        settings['whisper_threads']),
                    local_files_only=local_res
                )
            else:
                err['msg'] = str(e)
                return

        prompt = settings.get(f'initial_prompt_{detect_language}') if detect_language!='auto' else None
        segments, info = model.transcribe(
            audio_file,
            beam_size=settings['beam_size'],
            best_of=settings['best_of'],
            condition_on_previous_text=settings['condition_on_previous_text'],
            temperature=0.0 if int(settings['temperature']) == 0 else [0.0, 0.2, 0.4, 0.6,
                                                                       0.8, 1.0],
            vad_filter=bool(settings['vad']),
            vad_parameters=dict(
                min_silence_duration_ms=settings['overall_silence'],
                max_speech_duration_s=float('inf'),
                threshold=settings['overall_threshold'],
                speech_pad_ms=settings['overall_speech_pad_ms']
            ),
            word_timestamps=True,
            language=detect_language[:2] if detect_language!='auto' else None,
            initial_prompt=prompt if prompt else None
        )
        if detect_language=='auto' and info.language!=detect['langcode']:
            detect['langcode']='zh-cn' if info.language[:2]=='zh' else info.language
        nums=0
        for segment in segments:
            nums+=1
            if not Path(TEMP_DIR + f'/{os.getpid()}.lock'):
                return
            new_seg=[]
            for idx, word in enumerate(segment.words):
                new_seg.append({"start":int(word.start*1000),"end":int(word.end*1000),"word":word.word })
            raws.append({"words":new_seg,"text":segment.text})
            time_str=f'{ms_to_time_string(ms=segment.start*1000)} --> {ms_to_time_string(ms=segment.end*1000)}'
            q.put_nowait({"text": f'{nums}\n{time_str}\n{segment.text}\n\n', "type": "subtitle"})
            q.put_nowait({"text": f' {"字幕" if defaulelang == "zh" else "Subtitles"} {len(raws) + 1} ', "type": "logs"})
           
    except Exception as e:
        if detect_language=='auto':
            err['msg']='检测语言失败，请设置发声语言/Failed to detect language, please set the voice language'
        else:
            import traceback
            err['msg'] = traceback.format_exception(e)
    except BaseException as e:
        err['msg'] = str(e)
    finally:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except:
            pass
        time.sleep(2)