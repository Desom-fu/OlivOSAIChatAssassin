import os
import sys
import json


def flatten_dict(d, parent_key=''):
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}.{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key).items())
        else:
            items.append((new_key, v))
    return dict(items)


def clean_path(p):
    p = p.strip().strip('"').strip("'")
    p = p.replace('\\', '/')
    return p


def convert_file(input_path, output_path, mode='helpdoc'):
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print(f"  跳过: 顶层不是字典")
        return False

    result = {}

    if mode == 'helpdoc' or (mode == 'auto' and 'helpdoc' in data):
        helpdoc = data.get('helpdoc', {})
        if isinstance(helpdoc, dict):
            result = helpdoc
        else:
            print(f"  跳过: helpdoc不是字典")
            return False
    elif mode == 'flatten':
        result = flatten_dict(data)
    else:
        result = data

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"  转换完成: {len(result)} 条知识")
    return True


def process_directory(input_dir, output_dir, mode, recursive):
    os.makedirs(output_dir, exist_ok=True)

    json_files = []
    if recursive:
        for root, dirs, files in os.walk(input_dir):
            for f in files:
                if f.endswith('.json'):
                    json_files.append(os.path.join(root, f))
    else:
        for f in os.listdir(input_dir):
            if f.endswith('.json'):
                json_files.append(os.path.join(input_dir, f))

    if not json_files:
        print(f"  未找到JSON文件")
        return 0

    print(f"  找到 {len(json_files)} 个JSON文件")
    success_count = 0
    for input_path in json_files:
        filename = os.path.basename(input_path)
        output_path = os.path.join(output_dir, filename)
        print(f"  处理: {filename}")
        try:
            if convert_file(input_path, output_path, mode):
                success_count += 1
        except Exception as e:
            print(f"  错误: {e}")

    return success_count


def process_file(input_file, output_dir, mode):
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.basename(input_file)
    output_path = os.path.join(output_dir, filename)
    print(f"  处理: {filename}")
    try:
        if convert_file(input_file, output_path, mode):
            return 1
    except Exception as e:
        print(f"  错误: {e}")
    return 0


def input_path(prompt):
    print(prompt)
    print("  (可直接拖入文件或文件夹路径)")
    p = input("  > ")
    return clean_path(p)


def choose_mode():
    print("\n选择转换模式:")
    print("  [1] auto   - 自动检测，有helpdoc就提取，否则原样（默认）")
    print("  [2] helpdoc - 强制提取helpdoc字段")
    print("  [3] flatten - 递归展平所有嵌套字典")
    c = input("  > ").strip()
    if c == '2':
        return 'helpdoc'
    elif c == '3':
        return 'flatten'
    return 'auto'


def main():
    print("=" * 50)
    print("  知识库JSON格式转换工具")
    print("  (群聊刺客 Knowledge 格式)")
    print("=" * 50)

    while True:
        print("\n请选择操作:")
        print("  [1] 转换文件夹中所有JSON")
        print("  [2] 转换单个JSON文件")
        print("  [0] 退出")
        choice = input("  > ").strip()

        if choice == '0':
            break

        if choice not in ('1', '2'):
            print("无效选择")
            continue

        mode = choose_mode()

        if choice == '1':
            input_dir = input_path("\n输入文件夹路径:")
            if not os.path.isdir(input_dir):
                print(f"错误: 文件夹不存在: {input_dir}")
                continue

            recursive = False
            subdirs = [d for d in os.listdir(input_dir) if os.path.isdir(os.path.join(input_dir, d))]
            if subdirs:
                print(f"\n检测到 {len(subdirs)} 个子文件夹，是否递归处理？")
                print("  [1] 否，只处理当前目录（默认）")
                print("  [2] 是，递归处理所有子文件夹")
                r = input("  > ").strip()
                recursive = r == '2'

            stripped = input_dir.rstrip('/\\')
            output_dir = f"{stripped}_converted"
            print(f"\n输出目录: {output_dir}")
            print("-" * 50)
            count = process_directory(input_dir, output_dir, mode, recursive)
            print("-" * 50)
            print(f"完成: {count} 个文件转换成功")

        elif choice == '2':
            input_file = input_path("\n输入文件路径:")
            if not os.path.isfile(input_file):
                print(f"错误: 文件不存在: {input_file}")
                continue

            parent = os.path.dirname(input_file)
            output_dir = os.path.join(parent, "converted")
            print(f"\n输出目录: {output_dir}")
            print("-" * 50)
            count = process_file(input_file, output_dir, mode)
            print("-" * 50)
            print(f"完成: {count} 个文件转换成功")

        print(f"\n将输出目录中的文件复制到:")
        print(f"  ./plugin/data/OlivOSAIChatAssassin/Knowledge/")

    print("\n已退出")


if __name__ == '__main__':
    main()
